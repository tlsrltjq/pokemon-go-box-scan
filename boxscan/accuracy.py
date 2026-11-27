"""Measure how often scan candidates match what the user saved, and what automatic saving would do.

The values the user approved (and any later hand edits) are the reference. For every approved
candidate this compares the values the review form would have been prefilled with against them,
field by field, and applies the proposed automatic-save rules (`gate`) to see how many would have
been saved without a look and how many of those were wrong. Read-only: the box and scans are not
changed. Re-run `analyze` on old scans first to measure the current recognizer.
"""
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from . import level
from .analysis import RESULT, suspect_move
from .box import DATABASE, code_number

CORE = ("species", "cp", "max_hp", "iv", "fast", "charged")
FLAGS = ("lucky", "shadow", "max_state")
FIELDS = CORE + FLAGS
LABELS = {"species": "종", "cp": "CP", "max_hp": "최대 HP", "iv": "IV", "fast": "노말어택", "charged": "스페셜어택",
          "lucky": "반짝반짝", "shadow": "그림자", "max_state": "다이맥스"}
# Same threshold as the review form's orange border (scan.js doubtful()).
MIN_AGREEMENT = 0.6


def _name(move: dict | None) -> str | None:
    return (move.get("name") or move.get("value")) if move else None


def _iv(individual: dict) -> dict | None:
    ivs = [a["iv"] for a in individual.get("appraisals", []) if a.get("iv")]
    return ivs[-1] if ivs else None


def candidate_values(individual: dict) -> dict:
    """What the review form is prefilled with (scan.js candidateValues)."""
    moves = individual.get("moves") or {}
    value = lambda key: (individual.get(key) or {}).get("value")
    return {
        "species": value("species"),
        "cp": value("cp"),
        "max_hp": value("max_hp"),
        "iv": _iv(individual),
        "fast": _name(moves.get("fast")),
        "charged": sorted(n for n in map(_name, moves.get("charged") or []) if n),
        "lucky": value("lucky") is True,
        "shadow": value("shadow") is True,
        "max_state": value("max_state"),
        "mega": bool((individual.get("state") or {}).get("temp_evolution")),
    }


def saved_values(data: dict, mega: bool) -> dict:
    """Box values in the same shape. While mega evolved the screen showed the mega CP and HP."""
    moves = data.get("moves") or {}
    shown = (data.get("temp_observation") or {}) if mega else data
    return {
        "species": data.get("species"),
        "cp": shown.get("cp"),
        "max_hp": shown.get("max_hp"),
        "iv": data.get("iv"),
        "fast": _name(moves.get("fast")),
        "charged": sorted(n for n in map(_name, moves.get("charged") or []) if n),
        "lucky": bool(data.get("lucky")),
        "shadow": bool(data.get("shadow")),
        "max_state": data.get("max_state"),
    }


def compare(read: dict, saved: dict) -> dict[str, str]:
    """correct / wrong / missing (not read, but the box has a value) / empty (neither has one)."""
    result = {}
    for field in FIELDS:
        a, b = read.get(field), saved.get(field)
        if field == "charged":
            if not a:
                result[field] = "missing" if b else "empty"
            elif a == b:
                result[field] = "correct"
            else:
                result[field] = "missing" if set(a) < set(b) else "wrong"
        elif a in (None, "") and field not in ("lucky", "shadow"):
            result[field] = "missing" if b not in (None, "") else "empty"
        else:
            result[field] = "correct" if a == b else "wrong"
    return result


def _doubtful(item: dict | None) -> bool:
    if not item:
        return False
    if "corrected_from" in item:
        return True
    observations = item.get("observations") or 0
    return observations > 1 and item.get("votes", 0) / observations < MIN_AGREEMENT


def gate(individual: dict) -> list[str]:
    """Reasons a candidate would be listed for a check instead of being saved automatically.

    Proposed rule: the species, CP, max HP and IV must fix one level (the strongest cross-check of
    four readings), both move slots must hold moves the species can learn, and the votes must agree.
    """
    reasons = []
    read = candidate_values(individual)
    if not read["species"]:
        reasons.append("종을 읽지 못함")
    status = (individual.get("level") or {}).get("status")
    other_forms = (individual.get("level") or {}).get("other_forms") or {}
    if other_forms:
        reasons.append(f"다른 모습으로도 맞음({', '.join(other_forms)}): 모습 확인")
    elif status != level.RESOLVED:
        reasons.append(f"레벨 {status or '없음'}")
    if "corrected_from" in (individual.get("cp") or {}):
        reasons.append("CP를 HP·IV·강화 비용으로 고침")
    if read["iv"] is None:
        reasons.append("IV 없음")
    ivs = {tuple(sorted(a["iv"].items())) for a in individual.get("appraisals", []) if a.get("iv")}
    if len(ivs) > 1:
        reasons.append("한 개체에 IV가 여러 개 (두 마리가 합쳐졌을 수 있음)")
    moves = individual.get("moves") or {}
    if not read["fast"]:
        reasons.append("노말어택 없음")
    if not read["charged"]:
        reasons.append("스페셜어택 없음")
    if any(suspect_move(move) for move in [moves.get("fast"), *(moves.get("charged") or [])]):
        reasons.append("배울 수 없거나 사전에 없는 기술")
    for key in ("species", "cp", "max_hp"):
        item = individual.get(key)
        if item and "corrected_from" not in item and _doubtful(item):
            reasons.append(f"{LABELS[key]} 판독 일치율 낮음")
    return reasons


def _duplicates(individuals: list[dict]) -> set[int]:
    """Candidates of one scan that look like the same Pokémon (an individual split in two)."""
    seen: dict[tuple, list[int]] = {}
    for index, individual in enumerate(individuals):
        read = candidate_values(individual)
        if read["species"] and read["iv"] and read["max_hp"]:
            key = (read["species"], tuple(sorted(read["iv"].items())), read["max_hp"])
            seen.setdefault(key, []).append(index)
    return {index for group in seen.values() if len(group) > 1 for index in group}


def _truth(revisions: list[sqlite3.Row], approved: int) -> tuple[dict | None, bool]:
    """Values at the approval, with later hand edits applied until the next scan or removal."""
    truth, edited = None, False
    for row in revisions:
        if row["revision"] < approved:
            continue
        source = json.loads(row["source"]) if row["source"] else {}
        if row["revision"] == approved:
            truth = json.loads(row["after"]) if row["after"] else None
            continue
        if row["change"] == "deleted" or "scan_id" in source:
            break
        if source.get("kind") == "manual_edit" and row["after"] and truth is not None:
            after = json.loads(row["after"])
            if after != truth:
                edited = True
            truth = after
    return truth, edited


def _align(individuals: list[dict], index: int, frames: list[int]) -> int | None:
    """The candidate the approval was made for. Indices can shift when a scan is analyzed again,
    so follow the frames the approval recorded."""
    if not frames:
        return index if index < len(individuals) else None
    wanted = set(frames)
    overlap = [len(wanted & set(individual.get("frames", []))) for individual in individuals]
    if index < len(individuals) and overlap[index] and set(individuals[index].get("frames", [])) == wanted:
        return index
    best = max(range(len(overlap)), key=overlap.__getitem__, default=None)
    return best if best is not None and overlap[best] else None


def measure(data_dir: Path, scan_ids: list[str] | None = None) -> dict:
    root = data_dir.resolve()
    database = root / DATABASE
    if not database.is_file():
        raise FileNotFoundError(f"박스 데이터베이스가 없습니다: {database}")
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        approvals = db.execute("SELECT * FROM approvals ORDER BY created_at").fetchall()
        revisions: dict[str, list] = {}
        for row in db.execute("SELECT * FROM revisions ORDER BY pokemon_id, revision"):
            revisions.setdefault(row["pokemon_id"], []).append(row)
        codes = db.execute("SELECT * FROM codes").fetchall()

    by_scan: dict[str, list] = {}
    for row in approvals:
        by_scan.setdefault(row["scan_id"], []).append(row)
    scans = sorted(set(scan_ids) if scan_ids else set(by_scan))

    fields = {field: Counter() for field in FIELDS}
    report = {
        "scans": 0, "scans_without_result": [], "candidates": 0, "approved": 0, "unaligned": 0,
        "edited_after_approval": 0, "fields": fields,
        "auto": {"pass": 0, "pass_wrong": 0, "pass_missing": 0, "flagged": 0, "flagged_wrong": 0,
                 "reasons": Counter()},
        "unsaved": {"count": 0, "would_pass": 0},
        "matching": {"updates": 0, "code_read": 0, "code_same": 0, "code_other": 0},
        "mismatches": [],
    }
    for scan_id in scans:
        path = root / scan_id / RESULT
        if not path.is_file():
            report["scans_without_result"].append(scan_id)
            continue
        individuals = json.loads(path.read_text(encoding="utf-8")).get("individuals", [])
        report["scans"] += 1
        report["candidates"] += len(individuals)
        duplicates = _duplicates(individuals)
        used = set()
        for approval in by_scan.get(scan_id, []):
            history = revisions.get(approval["pokemon_id"], [])
            approved_row = next((r for r in history if r["revision"] == approval["revision"]), None)
            source = json.loads(approved_row["source"]) if approved_row and approved_row["source"] else {}
            index = _align(individuals, approval["individual"], source.get("frames") or [])
            truth, edited = _truth(history, approval["revision"])
            if index is None or truth is None:
                report["unaligned"] += 1
                continue
            used.add(index)
            report["approved"] += 1
            report["edited_after_approval"] += edited
            individual = individuals[index]
            read = candidate_values(individual)
            result = compare(read, saved_values(truth, read["mega"]))
            for field, status in result.items():
                fields[field][status] += 1
            reasons = gate(individual) + (["같은 스캔에 같은 종·IV·HP 후보가 또 있음"] if index in duplicates else [])
            wrong = [field for field in CORE if result[field] == "wrong"]
            missing = [field for field in CORE if result[field] == "missing"]
            auto = report["auto"]
            if reasons:
                auto["flagged"] += 1
                auto["flagged_wrong"] += bool(wrong)
                auto["reasons"].update(reasons)
            else:
                auto["pass"] += 1
                auto["pass_wrong"] += bool(wrong)
                auto["pass_missing"] += bool(missing)
            saved = saved_values(truth, read["mega"])
            for field in wrong + [f for f in FLAGS if result[f] == "wrong"]:
                report["mismatches"].append({"scan_id": scan_id, "individual": index, "field": field,
                                             "read": read[field], "saved": saved[field],
                                             "auto": not reasons, "edited_later": edited})
            if approved_row and approved_row["change"] == "updated":
                matching = report["matching"]
                matching["updates"] += 1
                number = code_number((individual.get("code") or {}).get("value"))
                if number:
                    matching["code_read"] += 1
                    same = any(c["pokemon_id"] == approval["pokemon_id"] and c["number"] == number
                               and c["species"] == (truth.get("species") or "") for c in codes)
                    matching["code_same" if same else "code_other"] += 1
        for index, individual in enumerate(individuals):
            if index not in used:
                report["unsaved"]["count"] += 1
                report["unsaved"]["would_pass"] += not gate(individual) and index not in duplicates
    return report


def _rate(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.1%})" if whole else f"{part}/0"


def format_report(report: dict, details: bool = False) -> list[str]:
    lines = [f"스캔 {report['scans']}개, 판독 후보 {report['candidates']}개, 저장과 연결된 후보 {report['approved']}개"]
    if report["scans_without_result"]:
        lines.append(f"분석 결과가 없는 스캔 {len(report['scans_without_result'])}개 (비웠거나 삭제함)")
    if report["unaligned"]:
        lines.append(f"다시 분석해 어느 후보였는지 찾지 못한 저장 {report['unaligned']}개 (제외)")
    if report["edited_after_approval"]:
        lines.append(f"저장 뒤에 직접 수정한 개체 {report['edited_after_approval']}개 (수정한 값을 정답으로 씀)")
    lines.append("")
    lines.append("항목별 (정답 = 사용자가 저장·수정한 값)")
    for field, counts in report["fields"].items():
        checked = counts["correct"] + counts["wrong"] + counts["missing"]
        lines.append(f"  {LABELS[field]:<6} 맞음 {_rate(counts['correct'], checked)}, 틀림 {counts['wrong']}, "
                     f"못 읽음 {counts['missing']}")
    auto = report["auto"]
    total = auto["pass"] + auto["flagged"]
    lines += ["", "자동 저장 규칙을 적용했다면"]
    lines.append(f"  자동 저장 {_rate(auto['pass'], total)} — 그중 값이 틀린 개체 {_rate(auto['pass_wrong'], auto['pass'])}, "
                 f"일부 못 읽음 {auto['pass_missing']}")
    lines.append(f"  확인 필요 {_rate(auto['flagged'], total)} — 그중 실제로 틀린 값이 있던 개체 {auto['flagged_wrong']}")
    for reason, count in auto["reasons"].most_common():
        lines.append(f"    {reason}: {count}")
    unsaved = report["unsaved"]
    lines.append(f"  저장하지 않은 후보 {unsaved['count']}개 중 자동 저장 규칙을 통과하는 것 {unsaved['would_pass']}개 "
                 "(자동이었다면 잘못 들어갔을 수 있음)")
    matching = report["matching"]
    if matching["updates"]:
        lines += ["", f"기존 개체 갱신 {matching['updates']}건: 별명 번호를 읽은 것 {matching['code_read']}건, "
                      f"번호가 고른 개체와 같음 {matching['code_same']}건, 다름 {matching['code_other']}건"]
    if details and report["mismatches"]:
        lines += ["", "틀린 값"]
        for item in report["mismatches"]:
            mark = "자동" if item["auto"] else "확인"
            lines.append(f"  [{mark}] {item['scan_id'][:8]} #{item['individual'] + 1} {LABELS[item['field']]}: "
                         f"읽음 {item['read']} → 저장 {item['saved']}" + (" (저장 뒤 수정)" if item["edited_later"] else ""))
    return lines


def to_json(report: dict) -> dict:
    """JSON-friendly copy (Counters become plain dicts)."""
    return json.loads(json.dumps(report, default=dict, ensure_ascii=False))

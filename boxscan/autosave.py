"""Automatic saving of scan candidates (optional, off by default).

The user asked (2026-11-27) to stop approving every candidate by hand: candidates that pass the
rules are saved on their own, and only doubtful ones are listed for a check. The rules are the ones
`accuracy` measures (`accuracy.gate`), plus checks against the box:

- a management number in the nickname picks the entry to update; the update is held for a check
  when the number has no entry, the entry was removed, or the values differ (species, IV, lower CP);
- without a number, a box entry of the same species and IV holds it for a check (an update or a
  second Pokémon cannot be told apart); otherwise it is saved as a new entry;
- two candidates of one scan with the same species, IV and HP are held (one Pokémon split in two).

Saving goes through the same `BoxStore.approve` as a manual save, with `kind: auto_save` and the
rule version in the history, and can be undone while the entry is unchanged
(`BoxStore.undo_approval`). With the setting off nothing is saved automatically; the scan page
still shows which candidates pass and can save them with one press.
"""
import json
import sys
import traceback
from pathlib import Path

from . import accuracy, review
from .analysis import RESULT

RULE_VERSION = "auto-save/1"
SETTINGS = "settings.json"


def enabled(root: Path) -> bool:
    try:
        return json.loads((root / SETTINGS).read_text(encoding="utf-8")).get("auto_save") is True
    except (OSError, ValueError, AttributeError):
        return False


def set_enabled(root: Path, value: bool) -> None:
    path = root / SETTINGS
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        settings = settings if isinstance(settings, dict) else {}
    except (OSError, ValueError):
        settings = {}
    settings["auto_save"] = bool(value)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(path)


def raw_values(individual: dict) -> dict:
    """The values the review form is prefilled with (scan.js candidateValues), as sent on save."""
    moves = individual.get("moves") or {}
    value = lambda key: (individual.get(key) or {}).get("value")
    name = lambda move: (move.get("name") or move.get("value")) if move else None
    plus = moves.get("mega_plus")
    ivs = [a["iv"] for a in individual.get("appraisals", []) if a.get("iv")]
    return {
        "species": value("species"), "cp": value("cp"), "max_hp": value("max_hp"),
        "iv": ivs[-1] if ivs else None,
        "fast_move": name(moves.get("fast")),
        "charged_moves": [n for n in map(name, moves.get("charged") or []) if n],
        "lucky": value("lucky") is True, "background": value("background") is True,
        "shadow": value("shadow") is True,
        "max_state": value("max_state") or None,
        "temp_evolution": (individual.get("state") or {}).get("temp_evolution"),
        "max_moves": individual.get("max_moves") or [],
        "mega_move": (plus.get("name") or f"{plus['value']}+") if plus else None,
        "mega_level": plus.get("mega_level") if plus else None,
        "nickname": value("nickname"),
    }


def plan(scan_id: str, individuals: list[dict], box, data) -> tuple[list[dict], dict[int, dict]]:
    """For every candidate: saved already, ready to save (new or which entry to update), or held
    for a check with the reasons. Also returns the normalized values to save, by candidate index."""
    duplicates = accuracy._duplicates(individuals)
    entries = None
    items, normalized = [], {}
    for index, individual in enumerate(individuals):
        approval = box.approval(scan_id, index)
        if approval is not None:
            items.append({"index": index, "status": "saved", "approval": approval})
            continue
        reasons = accuracy.gate(individual)
        if index in duplicates:
            reasons.append("같은 스캔에 같은 종·IV·HP 후보가 또 있음 (한 마리가 둘로 나뉘었을 수 있음)")
        values, target = None, None
        try:
            values = review.normalize(raw_values(individual), data)
        except ValueError as error:
            reasons.append(str(error))
        code = (individual.get("code") or {}).get("value")
        species = (values or {}).get("species") or (individual.get("species") or {}).get("value")
        if code and species:
            found = box.by_code(species, code)
            if found is None:
                reasons.append(f"별명의 관리 번호 {code}에 맞는 {species}가 박스에 없음")
            elif found["deleted"]:
                reasons.append(f"별명의 관리 번호 {code}의 {species}는 박스에서 제거됨")
            else:
                target = found
                if values:
                    reasons += review.warnings(found, values)
        elif values and values.get("species") and values.get("iv"):
            if entries is None:
                entries = box.list()
            same = [e for e in entries if e.get("species") == values["species"] and e.get("iv") == values["iv"]]
            if same:
                codes = ", ".join(e.get("code") or "-" for e in same)
                reasons.append(f"박스에 같은 종·IV 개체가 있음({codes}번): 갱신인지 새 개체인지 확인")
        item = {"index": index, "status": "check" if reasons else "ready", "reasons": reasons,
                "action": "update" if target else "new"}
        if target:
            item["target"] = {"id": target["id"], "code": target.get("code"), "species": target.get("species"),
                              "revision": target["revision"]}
        items.append(item)
        if values:
            normalized[index] = values
    return items, normalized


def source(job: dict, result: dict, index: int, kind: str | None = None) -> dict:
    """What a save records about where the values came from (the same for manual and automatic)."""
    candidate = result["individuals"][index]
    found = {
        "scan_id": job["id"],
        "individual": index,
        "filename": job["filename"],
        "frames": candidate["frames"],
        "recognizer": result.get("recognizer"),
        "analysis_created_at": result.get("created_at"),
        "candidate": {key: (candidate.get(key) or {}).get("value") for key in ("species", "cp", "max_hp")},
    }
    if kind:
        found |= {"kind": kind, "rule": RULE_VERSION}
    return found


def record_resources(box, candidate: dict, values: dict, data, scan_id: str, index: int) -> None:
    """Keep the candy, XL, energy and stardust read on screen with the saved candidate."""
    resources = dict(candidate.get("resources") or {})
    if not resources:
        return
    if resources.get("family") and values.get("species") and data:
        # Saved with a corrected species: the candy belongs to that species' family.
        expected = data.candy_name(values["species"])
        resources["family"] = resources["family"] if resources["family"] == expected else expected
    box.record_resources(resources, {"scan_id": scan_id, "individual": index})


def run(store, box, scan_id: str, data) -> dict:
    """Save every candidate that passes; return what was saved and what is held for a check."""
    from .box import Conflict

    job = store.get(scan_id)
    path = store.root / scan_id / RESULT
    if job is None or job["status"] != "ready" or not path.is_file():
        raise FileNotFoundError("분석을 마친 스캔을 찾을 수 없습니다.")
    result = json.loads(path.read_text(encoding="utf-8"))
    items, values = plan(scan_id, result["individuals"], box, data)
    saved, held = [], []
    for item in items:
        if item["status"] == "check":
            held.append(item)
        if item["status"] != "ready":
            continue
        target = item.get("target")
        try:
            entry = box.approve(scan_id, item["index"], values[item["index"]], source(job, result, item["index"], "auto_save"),
                                target["id"] if target else None, target["revision"] if target else None)
        except Conflict as error:  # changed meanwhile: leave it for a check
            held.append({**item, "status": "check", "reasons": [str(error)]})
            continue
        if not entry.get("duplicate"):
            record_resources(box, result["individuals"][item["index"]], values[item["index"]], data, scan_id,
                             item["index"])
        saved.append({**item, "status": "saved", "entry": {"id": entry["id"], "code": entry.get("code"),
                                                           "species": entry.get("species")}})
    return {"rule": RULE_VERSION, "saved": saved, "check": held}


def after_analysis(store, scan_id: str) -> None:
    """Called when a scan's analysis is done: save automatically when the user turned it on."""
    if not enabled(store.root):
        return
    try:
        from . import reference
        from .box import BoxStore

        data = reference.load(store.root)
        if data is None:  # without game data no level can be fixed, so nothing would pass
            return
        run(store, BoxStore(store.root), scan_id, data)
    except Exception:  # automatic saving never fails the analysis; the candidates stay for review
        traceback.print_exc(file=sys.stderr)

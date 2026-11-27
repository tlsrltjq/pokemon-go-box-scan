"""A Markdown file for the user's own AI assistant (Claude, ChatGPT and the like).

The app makes no AI call. The user downloads this file and gives it to an assistant they already
use, to ask questions such as which Pokémon to power up or which party to bring. Numbers come from
the app's calculations (levels from the game data, battle results from the raid and Max Battle
engines), and the file tells the assistant to quote them instead of recomputing or guessing.

Like the Excel export it holds the approved box and the candy record, never videos, frames, file
names or capture times. Unknown values are written as "-", not 0. Table cells are escaped so a
nickname cannot break the table.
"""
from datetime import datetime

from . import species as species_names
from .reference import Reference

MEDIA_TYPE = "text/markdown; charset=utf-8"
LEVEL_NOTES = {"AMBIGUOUS": "후보 여러 개", "CONFLICT": "맞는 레벨 없음", "MISSING_INPUT": "값 부족",
               "NO_REFERENCE": "기준 데이터 없음", "UNKNOWN_SPECIES": "종 미확인"}
MAX_NAMES = {"dynamax": "다이맥스", "gigantamax": "거다이맥스"}
NAME_MARKS = (("shiny", "*"), ("lucky", "♣"), ("background", "◆"))

GUIDE = """## 이 파일을 읽는 AI에게

- 이 파일은 사용자의 포켓몬 GO 박스와, 사용자의 앱(포켓몬 GO 박스 스캔)이 계산한 전투 결과입니다.
- **숫자는 다시 계산하지 마세요.** 레벨·CP·IV·피해·처치 시간은 앱이 게임 데이터와 전투 계산 엔진으로 낸 값입니다. 답할 때 이 값을 근거로 인용하고, 추측한 숫자를 만들지 마세요.
- 표에 없는 포켓몬이나 값(`-`)은 "자료에 없음"이라고 답하세요. 빈 값은 0이 아니라 모른다는 뜻입니다.
- 개체는 `종 + 관리 번호`(예: 마기라스 02)로 부르세요. 사용자는 게임 별명에 이 번호를 붙여 둡니다.
- 사용자는 레이드를 주로 **혼자** 합니다. 전투 결과는 혼자 기준이고, 보스를 쓰러뜨리는지 → 처치 시간 → 준 피해 순으로 고른 파티입니다.
- 계산 가정과 한계는 맨 아래 `계산 기준`에 있습니다. 실전 처치 시간과는 아직 대조하지 않은 계산값입니다.
- 강화 비용(별의모래·사탕), 기술머신, 최신 메타처럼 이 파일에 없는 정보는 일반 지식으로 답하되 이 파일의 값과 구분해서 말하세요."""

QUESTIONS = """## 질문 예시

- 별의모래 30만으로 누구를 먼저 키우면 레이드에 가장 도움이 될까?
- 지금 열린 레이드 중 내 박스로 혼자 깰 수 있는 보스는?
- 이 파티에서 한 마리만 바꾼다면 박스의 누구로 바꾸는 게 좋을까?
- 메가진화를 처음 해 볼 포켓몬을 하나 고른다면?"""


def filename() -> str:
    return f"pokemon-box-ai-{datetime.now().strftime('%Y%m%d-%H%M')}.md"


def _cell(value) -> str:
    """One table cell: "-" for unknown, no pipes or line breaks."""
    if value is None or value == "" or value == []:
        return "-"
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    return " ".join(text.split())


def _table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return lines


def _name(entry: dict) -> str:
    marks = "".join(mark for key, mark in NAME_MARKS if entry.get(key) is True)
    return (entry.get("species") or "종 미확인") + marks


def _level(level: dict | None) -> str | None:
    level = level or {}
    if level.get("value") is not None:
        return f"{level['value']:g}"
    candidates = level.get("candidates") or []
    if len(candidates) > 1:
        return "후보 " + ", ".join(f"{value:g}" for value in candidates)
    return LEVEL_NOTES.get(level.get("status"))


def _state(entry: dict, reference: Reference | None) -> str | None:
    parts = []
    if entry.get("shadow") is True:
        parts.append("그림자")
    if entry.get("max_state"):
        moves = ", ".join(f"{m['name']} {'Lv' + str(m['level']) if m.get('level') else '잠김' if m.get('locked') else ''}".strip()
                          for m in entry.get("max_moves") or [])
        parts.append(MAX_NAMES.get(entry["max_state"], entry["max_state"]) + (f"({moves})" if moves else ""))
    mega = species_names.mega_info(reference, entry)
    if mega:
        status = (f"메가 Lv.{mega['level']}" if mega["level"] else "메가진화해 봄") if mega["evolved"] else "메가 가능"
        parts.append(status + (f"(+기술 {', '.join(mega['plus_moves'])})" if mega["plus_moves"] and not mega["plus_off"] else ""))
    return ", ".join(parts) or None


def _move_name(move: dict | None, reference: Reference | None) -> str | None:
    """The saved name, or the game data's Korean name for a move stored only by its id."""
    if not move:
        return None
    move_id = move.get("move_id")
    return move.get("name") or ((reference.move_names or {}).get(move_id) if reference and move_id else None) or move_id


def _box(entries: list[dict], reference: Reference | None, candies: dict) -> list[str]:
    rows = []
    for entry in sorted(entries, key=lambda e: (e.get("species") or "", int(e.get("code") or 0))):
        iv = entry.get("iv")
        moves = entry.get("moves") or {}
        charged = [_move_name(m, reference) for m in moves.get("charged") or []]
        family = reference.candy_name(entry.get("species")) if reference else None
        rows.append([
            entry.get("code"), _name(entry),
            entry.get("form_name") if entry.get("form") not in (None, "Normal") else None,
            entry.get("cp"), entry.get("max_hp"), _level(entry.get("level")),
            f"{iv['attack']}/{iv['defense']}/{iv['stamina']}" if iv else None,
            f"{round((iv['attack'] + iv['defense'] + iv['stamina']) / 45 * 100)}%" if iv else None,
            _move_name(moves.get("fast"), reference), ", ".join(filter(None, charged)), _state(entry, reference), family,
        ])
    return [f"## 내 박스 ({len(entries)}마리)", "",
            "종 뒤 기호: `*` 이로치, `♣` 반짝반짝, `◆` 배경. IV는 공격/방어/HP(0~15). 사탕은 `계열`의 사탕을 씁니다.", "",
            *_table(["번호", "종", "모습", "CP", "최대 HP", "레벨", "IV", "IV%", "노말어택", "스페셜어택", "상태", "사탕 계열"], rows)]


def _resources(candies: dict) -> list[str]:
    dust = (candies.get("stardust") or {}).get("value")
    rows = [[family, values.get("candy"), values.get("xl"),
             f"{values['energy']} ({values.get('energy_kind') or '메가'})" if values.get("energy") is not None else None]
            for family, values in sorted((candies.get("candies") or {}).items())]
    lines = ["## 자원", "", f"- 별의모래: {dust:,}" if dust is not None else "- 별의모래: 기록 없음",
             "- 사탕은 스캔에서 저장할 때 화면에서 읽은 마지막 값입니다(진화 계열마다 하나).", ""]
    return lines + (_table(["계열", "사탕", "사탕XL", "메가 에너지"], rows) if rows else ["사탕 기록 없음"])


def _clear(summary: dict) -> str:
    if summary.get("clear_s"):
        clear = f"평균 {summary['clear_s']['avg']:.1f}초에 처치"
    elif summary.get("damage"):
        clear = f"처치 못 함, 평균 피해 {round(summary['damage']['avg']):,} (보스 HP {summary['boss_hp']:,}의 " \
                f"{round(summary['damage']['avg'] / summary['boss_hp'] * 100)}%)"
    else:
        return "추천할 수 있는 개체 없음"
    scenarios = summary.get("scenarios")
    return clear + (f" · 보스 기술 조합 {scenarios}가지 중 {summary.get('defeated_scenarios') or 0}가지에서 처치" if scenarios else "")


def _mega_line(comparison: dict | None) -> str | None:
    if not comparison or not comparison.get("options"):
        return None
    options = comparison["options"]
    base = options.get("off")
    parts = []
    for key, label in (("evolved", "메가진화해 본 개체"), ("all", "아직 메가진화 안 한 개체까지")):
        other = options.get(key)
        if not other or not other.get("member") or other == options.get("evolved") and key == "all":
            continue
        result = f"평균 {other['clear_s']:.1f}초에 처치" if other.get("clear_s") else f"평균 피해 {round(other['damage']):,}"
        parts.append(f"{label} 중 {other['member']}({other.get('code') or '-'}번)을 넣으면 {result}")
    if not parts:
        return None
    plain = (f"메가 없이 평균 {base['clear_s']:.1f}초" if base and base.get("clear_s")
             else f"메가 없이 평균 피해 {round(base['damage']):,}" if base else "")
    return "메가진화 비교: " + "; ".join(parts) + (f" ({plain})" if plain else "")


def _battles(raids: list[tuple[dict, dict]]) -> list[str]:
    lines = ["## 현재 레이드·맥스배틀 추천", "",
             "앱에 저장된 현재 보스 목록으로 계산했습니다. 날씨 보너스 없음, 혼자, 메가진화 없이(비교는 따로 표시), "
             "보스 스페셜어택은 피하는 기준(시간이 모자라면 다 맞기)입니다. 파티는 출전 순서대로입니다."]
    for boss, summary in raids:
        kind = "맥스배틀" if boss["kind"] == "max" else "레이드"
        lines += ["", f"### {boss['label']} · {kind} {boss['tier_name']} · {'/'.join(boss.get('types') or [])}"]
        members = []
        for member in summary.get("party") or []:
            text = f"{member['name']}({member.get('code') or '-'}번, Lv{member.get('level')})"
            text += f" {member.get('fast')}/{member.get('charged') or '스페셜어택 안 씀'}"
            if member.get("max_move"):
                text += f", {member['max_move']}"
            members.append(text)
        lines.append("- 추천: " + (" → ".join(members) if members else "없음"))
        lines.append(f"- 결과: {_clear(summary)}")
        if summary.get("dodge_comparison", {}) and summary["dodge_comparison"].get("chosen") == "tank":
            lines.append("- 회피: 다 맞으면서 하는 쪽이 시간 안에 더 깎습니다.")
        mega = _mega_line(summary.get("mega_comparison"))
        if mega:
            lines.append(f"- {mega}")
        if summary.get("shortfall"):
            lines.append(f"- {summary['shortfall']}")
    return lines


ASSUMPTIONS = [
    "레벨: 종·CP·최대 HP·IV를 게임 데이터의 종족값·CP 배율과 맞춰 구했습니다. 하나로 정해지지 않으면 후보를 적었습니다.",
    "레이드 계산: 혼자 기준, 전원 체력 가득·에너지 0에서 시작, 스페셜어택은 에너지가 차면 바로 사용, 보스 기술 조합은 모두 같은 비중으로 평균. 친구·파티 파워 보너스는 넣지 않았습니다.",
    "회피: 보스 스페셜어택을 피하면 일부 피해만 받고 잠깐 공격하지 못한다고 봅니다. 회피는 항상 성공한다고 가정합니다.",
    "메가진화: 한 번 메가진화한 개체는 언제든 다시 할 수 있다고 보고, 메가에너지·재사용 대기는 따지지 않습니다. 게임 데이터에 '+' 기술이 있는 메가는 쓸 수 있다고 가정합니다.",
    "맥스배틀·그림자 레이드의 일부 수치(맥스 보스 공격 간격·회피 50%, 그림자 격앙)는 공식 수치가 없어 커뮤니티 추정값입니다.",
    "실전 검증: 추천 결과를 실제 전투 처치 시간과 아직 대조하지 않았습니다.",
]


def markdown(entries: list[dict], reference: Reference | None, candies: dict | None, app_version: str,
             raids: list[tuple[dict, dict]] | None = None, engines: str | None = None,
             now: datetime | None = None) -> str:
    candies = candies or {"candies": {}, "stardust": None}
    stamp = (now or datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M")
    meta = [f"- 만든 시각: {stamp}", f"- 앱 버전: {app_version}",
            f"- 게임 데이터 버전: {reference.version if reference else '없음 (레벨·메가 정보가 빠질 수 있음)'}"]
    if engines:
        meta.append(f"- 계산 엔진: {engines}")
    lines = ["# 내 포켓몬 GO 박스 — AI 상담용 자료", "", *meta, "", GUIDE, "", *_resources(candies), "",
             *_box(entries, reference, candies), ""]
    if raids:
        lines += [*_battles(raids), ""]
    elif raids is not None:
        lines += ["## 현재 레이드·맥스배틀 추천", "", "저장된 현재 보스 목록이 없습니다. 앱의 `현재 레이드`에서 목록을 갱신하면 함께 들어갑니다.", ""]
    lines += ["## 계산 기준", "", *(f"- {text}" for text in ASSUMPTIONS), "", QUESTIONS, ""]
    return "\n".join(lines)

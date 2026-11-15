"""Excel export of the approved box (spec v1.2 section 12).

The workbook is for reading and sharing by hand. It is not a backup. Unknown values stay empty
instead of becoming 0. Every text cell is written as a string, so a nickname or species that
starts with "=" cannot run as a formula. Videos, frames, file names and other capture details are
not included.
"""
import io
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .reference import Reference

LEVEL_STATUS = {
    "RESOLVED_BY_CONSTRAINTS": "하나로 정해짐",
    "AMBIGUOUS": "후보 여러 개",
    "CONFLICT": "맞는 레벨 없음",
    "MISSING_INPUT": "값 부족",
    "NO_REFERENCE": "기준 데이터 없음",
    "UNKNOWN_SPECIES": "종 미확인",
}
HEADER_FILL = PatternFill("solid", fgColor="DBE7E2")
DATE_FORMAT = "yyyy-mm-dd hh:mm"
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def app_version() -> str:
    try:
        with (Path(__file__).resolve().parents[1] / "pyproject.toml").open("rb") as file:
            return tomllib.load(file)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def filename() -> str:
    return f"pokemon-box-{datetime.now().strftime('%Y%m%d-%H%M')}.xlsx"


def _local(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    return datetime.fromisoformat(stamp).astimezone().replace(tzinfo=None)


NAME_MARKS = (("shiny", "*"), ("lucky", "♣"), ("background", "◆"))
MAX_NAMES = {"dynamax": "다이맥스", "gigantamax": "거다이맥스"}


def _max_moves(entry: dict) -> str | None:
    moves = entry.get("max_moves") or []
    return ", ".join(f"{m['name']} {'레벨 ' + str(m['level']) if m.get('level') else '잠김' if m.get('locked') else ''}".strip()
                     for m in moves) or None


def _fusion(entry: dict) -> str | None:
    return ", ".join(f"{f['component']} → {f.get('target_name') or f['target']}" if f.get("component")
                     else (f.get("target_name") or f["target"]) for f in entry.get("fusion") or []) or None


def _flag(value) -> str | None:
    return "예" if value is True else "아니요" if value is False else None


def _display_name(entry: dict) -> str | None:
    if not entry.get("species"):
        return None
    return entry["species"] + "".join(mark for key, mark in NAME_MARKS if entry.get(key) is True)


def _move_label(move: dict | None) -> str | None:
    return f"{move['name']}{move.get('marker') or ''}" if move else None


def _sheet(workbook: Workbook, title: str, headers: list[str], rows: list[list], widths: list[int],
           formats: dict[int, str] | None = None, first: bool = False):
    sheet = workbook.active if first else workbook.create_sheet()
    sheet.title = title
    for column, header in enumerate(headers, 1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.data_type = "s"
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center")
    for row_number, row in enumerate(rows, 2):
        for column, value in enumerate(row, 1):
            cell = sheet.cell(row=row_number, column=column)
            if value is None:
                continue
            cell.value = value
            if isinstance(value, str):
                cell.data_type = "s"  # never a formula
            elif isinstance(value, datetime):
                cell.number_format = DATE_FORMAT
            elif formats and column in formats:
                cell.number_format = formats[column]
    for column, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "B2"
    if rows:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    return sheet


def workbook_bytes(entries: list[dict], reference: Reference | None, app_version: str) -> bytes:
    workbook = Workbook()
    pokemon_rows, move_rows = [], []
    for entry in entries:
        iv = entry.get("iv")
        level = entry.get("level") or {}
        moves = entry.get("moves") or {}
        charged = moves.get("charged") or []
        candidates = level.get("candidates") or []
        pokemon_rows.append([
            entry["code"], _display_name(entry), entry.get("species"), entry.get("dex"),
            entry.get("form_name") or (entry.get("form") if entry.get("form") != "Normal" else None),
            _flag(entry.get("shiny")), _flag(entry.get("lucky")), _flag(entry.get("background")),
            MAX_NAMES.get(entry.get("max_state")),
            entry.get("cp"), entry.get("max_hp"),
            "계산값" if entry.get("cp_source") == "calculated" else None,
            level.get("value"), LEVEL_STATUS.get(level.get("status"), level.get("status")),
            ", ".join(f"{value:g}" for value in candidates) if len(candidates) > 1 else None,
            iv["attack"] if iv else None, iv["defense"] if iv else None, iv["stamina"] if iv else None,
            (iv["attack"] + iv["defense"] + iv["stamina"]) / 45 if iv else None,
            _move_label(moves.get("fast")),
            _move_label(charged[0]) if len(charged) > 0 else None,
            _move_label(charged[1]) if len(charged) > 1 else None,
            _max_moves(entry), _fusion(entry),
            (entry.get("temp_observation") or {}).get("cp"),
            _local(entry.get("created_at")), _local(entry.get("updated_at")),
            entry["revision"], entry["id"],
        ])
        slots = [("노말어택", moves.get("fast"))] + [
            (f"스페셜어택 {index}" if len(charged) > 1 else "스페셜어택", move)
            for index, move in enumerate(charged, 1)]
        for slot, move in slots:
            if move:
                move_rows.append([entry["code"], entry.get("species"), slot, move.get("name"),
                                  move.get("move_id"), move.get("marker") or None, move.get("reason")])
        for move in entry.get("max_moves") or []:
            state = f"레벨 {move['level']}" if move.get("level") else "잠김" if move.get("locked") else None
            move_rows.append([entry["code"], entry.get("species"), "맥스기술", move["name"], None, None, state])

    _sheet(workbook, "포켓몬 목록",
           ["관리 번호", "이름 표시", "종", "도감 번호", "모습", "이로치", "반짝반짝", "배경", "다이맥스",
            "CP", "최대 HP", "CP 출처", "레벨", "레벨 판정", "레벨 후보",
            "IV 공격", "IV 방어", "IV HP", "IV %", "노말어택", "스페셜어택 1", "스페셜어택 2",
            "맥스기술", "합체·모습 변화", "메가진화 중 CP",
            "처음 저장", "마지막 갱신", "revision", "UUID"],
           pokemon_rows,
           [10, 16, 14, 9, 10, 8, 9, 7, 10, 8, 8, 9, 7, 14, 12, 8, 8, 8, 8, 16, 18, 18,
            34, 30, 12, 17, 17, 9, 38],
           {19: "0%"}, first=True)
    _sheet(workbook, "기술", ["관리 번호", "종", "슬롯", "기술", "기술 ID", "표시", "표시 이유"],
           move_rows, [10, 14, 10, 18, 24, 6, 48])
    notes = [
        ["내보낸 시각", datetime.now(timezone.utc).astimezone().replace(tzinfo=None, microsecond=0)],
        ["개체 수", len(entries)],
        ["앱 버전", app_version],
        ["기준 데이터 버전", reference.version if reference else None],
        ["기준 데이터 받은 시각", _local(reference.fetched_at) if reference else None],
        ["* 표시", "대단한 기술머신으로만 배울 수 있는 기술(커뮤니티 데이·이벤트·예전 기술), "
                   "그림자 포켓몬·정화된 포켓몬 전용 기술, 지금 배울 수 있는 기술 목록에 없는 기술"],
        ["모습", "기본 모습은 빈칸입니다. 알로라의 모습 등 다른 모습만 적습니다."],
        ["이름 표시", "종 이름 뒤에 이로치 *, 반짝반짝 포켓몬 ♣, 배경 ◆를 붙입니다."],
        ["CP 출처", "계산값: 메가진화 중에 찍은 개체라 화면 CP 대신 레벨로 계산한 기본 모습 CP입니다."],
        ["빈칸", "읽지 못했거나 확인하지 않은 값입니다. 0으로 채우지 않습니다."],
        ["포함하지 않은 정보", "영상, 프레임, 파일 이름, 촬영 시각 등 촬영 정보"],
        ["주의", "이 파일은 보기용입니다. 박스 복원에는 별도 백업을 사용하세요."],
        ["아직 없는 시트", "전투 추천 결과 (해당 기능 구현 후 추가)"],
    ]
    _sheet(workbook, "메타데이터", ["항목", "값"], notes, [22, 80])
    workbook["메타데이터"].freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()

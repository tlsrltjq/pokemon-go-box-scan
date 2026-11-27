"""Pokémon GO event calendar: events, raid hours, spotlight hours, community days, raid rotations.

Downloaded only when the user asks (the refresh button on the 현재 레이드 page or
`python -m boxscan raids update`), like the boss list: a plain GET of a public JSON file, kept under
`data/events/` in a folder named after its UTC time with the URL, SHA-256 and fetch time. A failed
download or validation keeps the previous calendar.

Source: ScrapedDuck's `events.json` (LeekDuck's event list, scraped with LeekDuck's permission).
Its terms: credit ScrapedDuck and LeekDuck, no paywall, no advertising. Event names are English as
published; Pokémon names in raid, spotlight and community day details are mapped to Korean through
the reference data when the English name matches, and kept as published otherwise.

Times: most `start`/`end` values have no offset and are the local wall-clock time everywhere (the
way the game schedules events); values ending in `Z` are UTC and are converted to this computer's
local time.
"""
import hashlib
import json
import re
import shutil
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .reference import MAX_BYTES, Reference

SOURCE_NAME = "ScrapedDuck"
SOURCE_HOME = "https://github.com/bigfoott/ScrapedDuck"
SOURCE_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json"
CREDIT = "ScrapedDuck · LeekDuck"
USER_AGENT = "boxscan-events/0.1"
KEEP_VERSIONS = 10
FILE = "events.json"

TYPE_NAMES = {
    "raid-hour": "레이드 아워", "raid-day": "레이드 데이", "raid-battles": "레이드 보스",
    "raid-weekend": "레이드 위크엔드", "max-battles": "맥스배틀", "max-mondays": "맥스 먼데이",
    "pokemon-spotlight-hour": "스포트라이트 아워", "community-day": "커뮤니티 데이",
    "event": "이벤트", "season": "시즌", "go-pass": "GO 패스", "go-battle-league": "GO 배틀리그",
    "pokemon-go-tour": "GO 투어", "pokemon-go-fest": "GO Fest", "timed-research": "기간 한정 리서치",
    "research": "리서치", "special-research": "스페셜 리서치", "choose-your-path": "선택 이벤트",
    "research-day": "리서치 데이", "elite-raids": "엘리트 레이드", "safari-zone": "사파리존",
    "live-event": "현장 이벤트", "city-safari": "시티 사파리", "wild-area": "와일드 에어리어",
}
# Types that are about raids or Max Battles: shown first and matched to the boss cards.
RAID_TYPES = {"raid-hour", "raid-day", "raid-battles", "raid-weekend", "max-battles", "max-mondays", "elite-raids"}
REGIONAL = {"Alolan": "알로라", "Galarian": "가라르", "Hisuian": "히스이", "Paldean": "팔데아"}


class EventListError(ValueError):
    """The downloaded calendar cannot be used. The message is shown to the user."""


def root(data_dir: Path) -> Path:
    return data_dir / "events"


def update(data_dir: Path, fetch=None, now: datetime | None = None) -> dict:
    fetch = fetch or _fetch
    now = now or datetime.now(timezone.utc)
    data = fetch(SOURCE_URL)
    events = parse(data)
    if not events:
        raise EventListError("받은 이벤트 목록이 비어 있습니다. 기존 달력을 유지합니다.")
    version = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{SOURCE_NAME}"
    folder = root(data_dir) / version
    folder.mkdir(parents=True, exist_ok=True)
    (folder / FILE).write_bytes(data)
    manifest = {"version": version, "source": SOURCE_NAME, "source_home": SOURCE_HOME, "credit": CREDIT,
                "fetched_at": now.isoformat(timespec="seconds"),
                "file": {"url": SOURCE_URL, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
                "count": len(events)}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    partial = root(data_dir) / "current.partial"
    partial.write_text(version, encoding="utf-8")
    partial.replace(root(data_dir) / "current")
    folders = sorted((p for p in root(data_dir).iterdir() if p.is_dir() and (p / "manifest.json").is_file()),
                     key=lambda p: p.name, reverse=True)
    for old in folders[KEEP_VERSIONS:]:
        if old.name != version:
            shutil.rmtree(old, ignore_errors=True)
    return manifest


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"예상보다 큰 파일입니다: {url}")
    return data


def parse(data: bytes) -> list[dict]:
    """The usable events of a download: each needs an id, a name and at least a start or an end."""
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise EventListError("이벤트 목록을 JSON으로 읽지 못했습니다.") from None
    if not isinstance(value, list):
        raise EventListError("이벤트 목록의 형식이 예상과 다릅니다(배열이 아님).")
    events = []
    for item in value:
        if not isinstance(item, dict) or not item.get("eventID") or not item.get("name"):
            continue
        start, end = _time(item.get("start")), _time(item.get("end"))
        if start is None and end is None:
            continue  # announced without a date yet
        events.append({"id": str(item["eventID"]), "name": str(item["name"]), "type": str(item.get("eventType") or ""),
                       "heading": item.get("heading"), "link": item.get("link"), "image": item.get("image"),
                       "start": start, "end": end, "extra": item.get("extraData") or {}})
    return events


def _time(value) -> datetime | None:
    """A naive local datetime: offset-less values are local wall-clock time, "Z" values are UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def current(data_dir: Path) -> dict | None:
    pointer = root(data_dir) / "current"
    if not pointer.is_file():
        return None
    folder = root(data_dir) / pointer.read_text(encoding="utf-8").strip()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    return {"manifest": manifest, "events": parse((folder / FILE).read_bytes())}


def _names(reference: Reference | None) -> dict[str, int]:
    """English species name (letters only, lower case) -> pokemon id, from the game master names."""
    if reference is None:
        return {}
    return {re.sub(r"[^a-z]", "", forms[0].name.lower()): pokemon_id
            for pokemon_id, forms in reference.stats.items() if forms}


def pokemon(name: str, reference: Reference | None, english: dict[str, int] | None = None) -> dict:
    """Map a published name ("Shadow Tornadus (Incarnate)", "Mega Charizard Y", "Alolan Raichu") to
    the Korean species name and its state. Unknown names keep the published text."""
    english = _names(reference) if english is None else english
    text = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
    words = text.split()
    shadow = bool(words) and words[0] == "Shadow"
    words = words[1:] if shadow else words
    temp = None
    if words and words[0] in ("Mega", "Primal"):
        temp = "PRIMAL" if words[0] == "Primal" else "MEGA"
        words = words[1:]
        if temp == "MEGA" and words and words[-1] in ("X", "Y"):
            temp = f"MEGA_{words[-1]}"
            words = words[:-1]
    region = REGIONAL.get(words[0]) if words else None
    words = words[1:] if region else words
    key = re.sub(r"[^a-z]", "", " ".join(words).lower())
    pokemon_id = english.get(key)
    korean = None
    if pokemon_id is not None and reference is not None:
        korean = next((k for k, v in reference.korean_names.items() if v == pokemon_id), None)
    if korean is None:
        return {"name": name, "species": None, "dex": None, "temp": temp, "shadow": shadow, "mapped": False}
    label = korean
    if temp == "PRIMAL":
        label = f"원시{korean}"
    elif temp:
        label = f"메가{korean}" + temp.removeprefix("MEGA").removeprefix("_")
    if region:
        label = f"{label} ({region})"
    return {"name": ("그림자 " if shadow else "") + label, "species": korean, "dex": pokemon_id, "temp": temp,
            "shadow": shadow, "mapped": True}


def view(listing: dict | None, reference: Reference | None, now: datetime | None = None,
         keep_past_days: int = 1) -> dict:
    """The calendar for the page: events that are on now or still to come (and the last day's),
    soonest first, with Korean type names and mapped Pokémon."""
    if listing is None:
        return {"version": None, "events": []}
    now = now or datetime.now()
    english = _names(reference)
    shown = []
    for event in listing["events"]:
        start, end = event["start"], event["end"]
        if end is not None and end < now - timedelta(days=keep_past_days):
            continue
        status = ("ended" if end is not None and end <= now
                  else "live" if start is None or start <= now else "upcoming")
        extra = event["extra"]
        raid = extra.get("raidbattles") or {}
        spotlight = extra.get("spotlight") or {}
        community = extra.get("communityday") or {}
        item = {
            "id": event["id"], "name": event["name"], "type": event["type"],
            "type_name": TYPE_NAMES.get(event["type"], event.get("heading") or event["type"] or "이벤트"),
            "raid": event["type"] in RAID_TYPES, "link": event["link"], "status": status,
            "start": start.isoformat(timespec="minutes") if start else None,
            "end": end.isoformat(timespec="minutes") if end else None,
            "bosses": [pokemon(str(b.get("name", "")), reference, english) | {"shiny": b.get("canBeShiny") is True}
                       for b in raid.get("bosses") or [] if isinstance(b, dict) and b.get("name")],
            "spotlight": ({**pokemon(str(spotlight["name"]), reference, english), "bonus": spotlight.get("bonus")}
                          if spotlight.get("name") else None),
            "spawns": [pokemon(str(s.get("name", "")), reference, english)["name"]
                       for s in community.get("spawns") or [] if isinstance(s, dict) and s.get("name")],
            "bonuses": [str(b.get("text")) for b in community.get("bonuses") or [] if isinstance(b, dict) and b.get("text")],
        }
        shown.append(item)
    shown.sort(key=lambda e: (e["status"] == "ended", e["start"] or "", e["end"] or ""))
    manifest = listing["manifest"]
    return {"version": manifest["version"], "source": manifest["source"], "source_home": manifest["source_home"],
            "credit": manifest.get("credit", CREDIT), "fetched_at": manifest["fetched_at"],
            "now": now.isoformat(timespec="minutes"), "events": shown}

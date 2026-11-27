"""Current raid and max battle bosses, mapped to the reference data, with cached recommendations.

The list is downloaded only when the user asks for it (`python -m boxscan raids update` or the
refresh button on the 현재 레이드 page). Nothing about the user or the box is sent: the request is
a plain GET of a public JSON file. Each download is kept under `data/raid-bosses/` in a folder
named after its UTC time and source, with the raw files, their URL, SHA-256 and fetch time. The
previous list stays current if a download or validation fails.

Source (compared on 2026-11-25, see README): pokemon-go-api's `raidboss.json`, rebuilt about
eight times a day from LeekDuck's raid boss page, and `maxbattles.json`, rebuilt from Snacknap.
Its entries carry game master ids and forms (`TORNADUS_INCARNATE`, `BEEDRILL_MEGA`) and explicit
levels (`shadow_lvl5`, `legendary_mega`, `primal`), so they map onto the reference data without
parsing English display names.

Mapping never drops a boss quietly: every entry comes back with status "ok" or "failed" and a
reason. The source's catch CP range (level 20, IV 10-15) is checked against the reference stats;
it settles costume forms that the reference merges into the regular form.
"""
import hashlib
import json
import re
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import level as level_rules
from . import max_battle, raid
from .forms import display_name
from .reference import MAX_BYTES, Reference
from .species import TEMP_NAMES

SOURCE_NAME = "pokemon-go-api"
SOURCE_HOME = "https://github.com/pokemon-go-api/pokemon-go-api"
SOURCES = {
    "raidboss.json": "https://pokemon-go-api.github.io/pokemon-go-api/api/raidboss.json",
    "maxbattles.json": "https://pokemon-go-api.github.io/pokemon-go-api/api/maxbattles.json",
}
USER_AGENT = "boxscan-raid-bosses/0.1"
KEEP_VERSIONS = 20
KEEP_CACHE = 300
CATCH_LEVEL = 20.0  # raid and max battle catch CP is shown at level 20

# Source level -> (raid tier in raid.RULESET, shadow raid)
RAID_LEVELS = {
    "lvl1": ("1", False), "lvl3": ("3", False), "lvl5": ("5", False),
    "ex": ("5", False), "ultra_beast": ("5", False),
    "mega": ("mega", False), "legendary_mega": ("mega_legendary", False),
    "super_mega": ("mega_legendary", False), "primal": ("mega_legendary", False),
    "shadow_lvl1": ("1", True), "shadow_lvl3": ("3", True), "shadow_lvl5": ("5", True),
}
LEVEL_NOTES = {
    "ex": "출처 등급 EX 레이드: 5성 HP·CP 배율로 계산합니다.",
    "ultra_beast": "출처 등급 울트라비스트: 5성 HP·CP 배율로 계산합니다.",
    "super_mega": "출처 등급 슈퍼 메가: 메가 전설 HP·CP 배율로 계산합니다.",
}
MAX_LEVELS = {"tier_1": "1", "tier_2": "2", "tier_3": "3", "tier_4": "4", "tier_5": "5", "tier_6": "gmax"}
TEMP_SUFFIX = re.compile(r"_(MEGA_X|MEGA_Y|MEGA|PRIMAL)$")
REGIONAL_WORDS = {"Alolan": "Alola", "Galarian": "Galarian", "Hisuian": "Hisuian", "Paldean": "Paldea"}
TIER_ORDER = ["mega_legendary", "5", "mega", "3", "1"]


class BossListError(ValueError):
    """The downloaded list cannot be used. The message is shown to the user."""


def root(data_dir: Path) -> Path:
    return data_dir / "raid-bosses"


# ---------------------------------------------------------------- download and cache

def update(data_dir: Path, fetch=None, now: datetime | None = None) -> dict:
    """Download the lists, validate them, keep them in a dated folder and make it current."""
    fetch = fetch or _fetch
    now = now or datetime.now(timezone.utc)
    files, errors = {}, {}
    for name, url in SOURCES.items():
        try:
            files[name] = fetch(url)
        except (OSError, ValueError) as error:
            if name == "raidboss.json":
                raise
            errors[name] = str(error)  # the max battle list is optional
    raw = {name: _decode(name, data) for name, data in files.items()}
    raids = parse_raids(raw["raidboss.json"])
    max_list = parse_max(raw["maxbattles.json"]) if "maxbattles.json" in raw else []
    if not raids:
        raise BossListError("받은 레이드 보스 목록이 비어 있습니다. 기존 목록을 유지합니다.")
    version = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{SOURCE_NAME}"
    folder = root(data_dir) / version
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    manifest = {
        "version": version,
        "source": SOURCE_NAME,
        "source_home": SOURCE_HOME,
        "fetched_at": now.isoformat(timespec="seconds"),
        "files": {name: {"url": SOURCES[name], "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                  for name, data in files.items()},
        "errors": errors,
        "counts": {"raids": len(raids), "max_battles": len(max_list)},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    partial = root(data_dir) / "current.partial"
    partial.write_text(version, encoding="utf-8")
    partial.replace(root(data_dir) / "current")
    _prune_versions(data_dir, version)
    return manifest


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"예상보다 큰 파일입니다: {url}")
    return data


def _decode(name: str, data: bytes) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BossListError(f"{name}을(를) JSON으로 읽지 못했습니다.") from None
    if not isinstance(value, dict) or not isinstance(value.get("currentList"), (dict, list)):
        raise BossListError(f"{name}의 형식이 예상과 다릅니다(currentList 없음).")
    return value


def _prune_versions(data_dir: Path, keep: str) -> None:
    folders = sorted((p for p in root(data_dir).iterdir() if p.is_dir() and p.name != "cache"
                      and (p / "manifest.json").is_file()), key=lambda p: p.name, reverse=True)
    for folder in folders[KEEP_VERSIONS:]:
        if folder.name != keep:
            shutil.rmtree(folder, ignore_errors=True)


def current(data_dir: Path) -> dict | None:
    """The current download: manifest plus parsed (unmapped) raid and max battle entries."""
    pointer = root(data_dir) / "current"
    if not pointer.is_file():
        return None
    folder = root(data_dir) / pointer.read_text(encoding="utf-8").strip()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    raids = parse_raids(_decode("raidboss.json", (folder / "raidboss.json").read_bytes()))
    max_path = folder / "maxbattles.json"
    max_list = parse_max(_decode("maxbattles.json", max_path.read_bytes())) if max_path.is_file() else []
    return {"manifest": manifest, "raids": raids, "max_battles": max_list}


# ---------------------------------------------------------------- parsing

def _items(current_list) -> list[tuple[str, dict]]:
    if isinstance(current_list, list):  # PHP writes an empty object as []
        return [(str(item.get("level", "")), item) for item in current_list if isinstance(item, dict)]
    return [(str(level), item) for level, items in current_list.items() if isinstance(items, list)
            for item in items if isinstance(item, dict)]


def _entry(level: str, item: dict) -> dict:
    names = item.get("names") if isinstance(item.get("names"), dict) else {}
    cp_range = item.get("cpRange")
    return {
        "level": level,
        "id": str(item.get("id") or "").upper(),
        "form": str(item["form"]).upper() if item.get("form") else None,
        "costume": item.get("costume"),
        "name_en": names.get("English"),
        "name_ko": names.get("Korean"),
        "types": [str(t) for t in item.get("types") or []],
        "cp_range": cp_range if isinstance(cp_range, list) and len(cp_range) == 2 else None,
        "shiny": item.get("shiny"),
    }


def parse_raids(raw: dict) -> list[dict]:
    return [_entry(level, item) for level, item in _items(raw["currentList"])]


def parse_max(raw: dict) -> list[dict]:
    return [_entry(level, item) for level, item in _items(raw["currentList"])]


# ---------------------------------------------------------------- mapping

def _enum_ids(reference: Reference) -> dict[str, int]:
    """Game master pokemonId (BULBASAUR, MR_MIME) -> dex number, from the reference stats."""
    return {forms[0].name.upper(): pokemon_id for pokemon_id, forms in reference.stats.items() if forms}


def _korean(reference: Reference, pokemon_id: int) -> str | None:
    return next((name for name, number in reference.korean_names.items() if number == pokemon_id), None)


def _catch_cp(reference: Reference, base) -> int | None:
    cpm = reference.cpm.get(CATCH_LEVEL)
    if not cpm or base is None:
        return None
    return level_rules.cp(base, {"attack": 15, "defense": 15, "stamina": 15}, cpm)


def _form_stats(reference: Reference, species: str, form: str | None):
    options = level_rules._stat_options(reference, species, None, form)
    return options[0] if options else None


def _known_form(reference: Reference, pokemon_id: int, form: str) -> bool:
    if any(f.form == form for f in reference.stats.get(pokemon_id, [])):
        return True
    return any(c["target"] == form and c.get("stats") for c in (reference.form_changes or {}).get(pokemon_id, []))


def _failed(entry: dict, kind: str, reason: str, **extra) -> dict:
    return {"kind": kind, "status": "failed", "reason": reason, "source": entry,
            "label": entry.get("name_ko") or entry.get("name_en") or entry.get("id") or "이름 없음", **extra}


def map_boss(reference: Reference, entry: dict, kind: str = "raid") -> dict:
    """Map one source entry to species, form, mega state, tier and shadow, or say why it failed."""
    notes: list[str] = []
    if kind == "raid":
        if entry["level"] not in RAID_LEVELS:
            return _failed(entry, kind, f"모르는 레이드 등급입니다: {entry['level'] or '없음'}")
        tier, shadow = RAID_LEVELS[entry["level"]]
        if entry["level"] in LEVEL_NOTES:
            notes.append(LEVEL_NOTES[entry["level"]])
    else:
        if entry["level"] not in MAX_LEVELS:
            return _failed(entry, kind, f"모르는 맥스배틀 등급입니다: {entry['level'] or '없음'}")
        tier, shadow = MAX_LEVELS[entry["level"]], False
    pokemon_id = _enum_ids(reference).get(entry["id"])
    if pokemon_id is None:
        return _failed(entry, kind, f"기준 데이터에서 {entry['id'] or '이름 없는'} 종을 찾지 못했습니다.")
    species = _korean(reference, pokemon_id)
    if species is None:
        return _failed(entry, kind, f"도감 번호 {pokemon_id}의 한국어 이름이 기준 데이터에 없습니다.")

    temp = None
    rest = entry["form"] or entry["id"]
    match = TEMP_SUFFIX.search(rest)
    if match:
        temp, rest = match[1], rest[:match.start()]
        if kind != "raid":
            return _failed(entry, kind, "맥스배틀 보스에 메가진화 모습이 붙어 있습니다.")
        if temp not in (reference.temp_stats or {}).get(pokemon_id, {}):
            return _failed(entry, kind, f"기준 데이터에 {raid.temp_name(species, temp)}의 능력치가 없습니다.")
        if shadow:
            return _failed(entry, kind, "그림자 레이드 보스에 메가진화 모습이 붙어 있습니다.")
    code = _form_code(entry["id"], rest)
    if kind == "max" and code == "Normal" and entry.get("name_en"):
        code = next((form for word, form in REGIONAL_WORDS.items() if word in entry["name_en"]), "Normal")
    expected = entry["cp_range"][1] if entry.get("cp_range") else None

    form, variant = None, None
    if code != "Normal":
        if _known_form(reference, pokemon_id, code):
            form = code
        else:
            regular = _catch_cp(reference, _form_stats(reference, species, None))
            if expected is not None and regular is not None and abs(regular - expected) <= 1:
                variant = code
                notes.append(f"모습 {code}은(는) 기준 데이터에 따로 없어 기본 모습 능력치로 계산합니다(출처 CP 범위 일치).")
            else:
                return _failed(entry, kind, f"모습 {code}을(를) 기준 데이터에서 찾지 못했습니다.", species=species)
    if expected is not None:
        calculated = _catch_cp(reference, _form_stats(reference, species, form))
        if calculated is not None and abs(calculated - expected) > 1:
            other = [f.form for f in reference.stats.get(pokemon_id, [])
                     if abs((_catch_cp(reference, f) or 0) - expected) <= 1]
            if kind == "max" and form is None and len(other) == 1:
                form = None if other[0] == "Normal" else other[0]
                notes.append(f"출처에 모습 정보가 없어 CP 범위가 맞는 {display_name(pokemon_id, form) or '기본 모습'}으로 계산합니다.")
            else:
                notes.append(f"출처 최대 CP {expected}와 기준 데이터로 계산한 CP {calculated}가 다릅니다. 모습·능력치를 확인하세요.")

    tiers = raid.RULESET["tiers"] if kind == "raid" else max_battle.RULESET["tiers"]
    form_name = display_name(pokemon_id, form)
    name = (raid.temp_name(species, temp) if temp else species) + (f" ({form_name})" if form_name else "")
    return {
        "kind": kind, "status": "ok", "reason": None, "source": entry, "notes": notes,
        "label": ("그림자 " if shadow else "") + name,
        "species": species, "pokemon_id": pokemon_id, "form": form, "form_name": form_name,
        "temp": temp, "temp_name": TEMP_NAMES.get(temp) if temp else None, "variant": variant,
        "tier": tier, "tier_name": tiers[tier]["name"], "shadow": shadow,
        "types": [raid.TYPE_NAMES.get(t, t) for t in raid._types(reference, pokemon_id, form, temp)],
    }


def _form_code(name: str, form: str | None) -> str:
    form = str(form or "Normal")
    form = form.removeprefix(f"{name}_") if form.startswith(f"{name}_") else form
    if form == name:
        return "Normal"
    return "Normal" if form.upper() == "NORMAL" else form.title()


def mapped(reference: Reference, listing: dict) -> dict:
    """Every boss of the current list with its mapping, raids grouped in tier order."""
    raids = [map_boss(reference, entry, "raid") for entry in listing["raids"]]
    for index, boss in enumerate(raids):
        boss["index"] = index
    max_list = [map_boss(reference, entry, "max") for entry in listing["max_battles"]]
    for index, boss in enumerate(max_list):
        boss["index"] = index

    def order(boss):
        tier = boss.get("tier")
        return (boss.get("shadow", False), TIER_ORDER.index(tier) if tier in TIER_ORDER else len(TIER_ORDER),
                boss["index"])

    manifest = listing["manifest"]
    return {
        "version": manifest["version"], "source": manifest["source"], "source_home": manifest.get("source_home"),
        "fetched_at": manifest["fetched_at"], "urls": {k: v["url"] for k, v in manifest["files"].items()},
        "errors": manifest.get("errors") or {},
        "raids": sorted(raids, key=order),
        "max_battles": sorted(max_list, key=lambda b: (-list(MAX_LEVELS.values()).index(b["tier"])
                                                     if b.get("tier") in MAX_LEVELS.values() else 1, b["index"])),
        "failed": sum(1 for b in raids + max_list if b["status"] != "ok"),
    }


# ---------------------------------------------------------------- recommendations with a cache

def box_fingerprint(entries: list[dict]) -> str:
    """Changes whenever an entry is added, edited, renumbered or removed."""
    return hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _options(kind: str, raw: dict) -> dict:
    weather = raw.get("weather") or ""
    if weather not in raid.WEATHER_NAMES:
        raise raid.RaidError("날씨를 다시 선택하세요.")
    trainers = raw.get("trainers", 1)
    if isinstance(trainers, str) and trainers.strip().isdigit():
        trainers = int(trainers)
    if not isinstance(trainers, int) or isinstance(trainers, bool) or not 1 <= trainers <= 20:
        raise raid.RaidError("함께하는 트레이너는 1~20명으로 입력하세요.")
    if kind == "max":
        return {"weather": weather, "trainers": min(trainers, 4)}
    return {"weather": weather, "trainers": trainers, "include_mega": raw.get("include_mega") is True}


def recommend(data_dir: Path, reference: Reference, entries: list[dict], boss: dict, raw_options: dict) -> dict:
    """The recommendation for one mapped boss, from the cache when nothing it depends on changed."""
    if boss["status"] != "ok":
        raise raid.RaidError(boss["reason"])
    kind = boss["kind"]
    options = _options(kind, raw_options)
    key_data = {
        "engine": raid.ENGINE_VERSION if kind == "raid" else max_battle.ENGINE_VERSION,
        "ruleset": raid.RULESET["version"] if kind == "raid" else max_battle.RULESET["version"],
        "reference": reference.version, "box": box_fingerprint(entries), "options": options,
        "boss": {k: boss[k] for k in ("kind", "species", "form", "temp", "tier", "shadow")},
    }
    key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()[:32]
    cache = root(data_dir) / "cache"
    path = cache / f"{key}.json"
    if path.is_file():
        try:
            return {**json.loads(path.read_text(encoding="utf-8")), "cached": True}
        except (OSError, ValueError):
            pass
    if kind == "raid":
        report = raid.recommend(reference, entries, boss["species"], boss["tier"], form=boss["form"],
                                temp=boss["temp"], weather=options["weather"], include_mega=options["include_mega"],
                                shadow={} if boss["shadow"] else None, trainers=options["trainers"])
    else:
        report = max_battle.recommend(reference, entries, boss["species"], boss["tier"], form=boss["form"],
                                      weather=options["weather"], trainers=options["trainers"])
    result = {"options": options, "summary": summary(kind, report), "computed_at":
              datetime.now(timezone.utc).isoformat(timespec="seconds")}
    cache.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    partial.replace(path)
    _prune_cache(cache)
    return {**result, "cached": False}


def _prune_cache(cache: Path) -> None:
    files = sorted(cache.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[KEEP_CACHE:]:
        path.unlink(missing_ok=True)


def summary(kind: str, report: dict) -> dict:
    """What the 현재 레이드 page shows: the party, the expected damage and the versions."""
    members = []
    for member in report["party"]:
        item = {k: member.get(k) for k in ("order", "name", "code", "level", "iv", "shadow", "fast", "charged",
                                           "damage")}
        if kind == "raid":
            item["mega"] = member.get("mega", {}).get("name") if member.get("mega") else None
        else:
            item["max_move"] = (member.get("max_attack") or {}).get("name")
            item["plan"] = member.get("plan")
        members.append(item)
    result = report.get("result")
    out = {
        "party": members,
        "boss_moves": report["boss"].get("moves"),
        "damage": result["damage"] if result else None,
        "clear_s": result.get("clear_s") if result else None,
        "boss_hp": report["boss"]["hp"],
        "defeated_scenarios": result.get("defeated_scenarios") if result else None,
        "scenarios": len(report.get("scenarios") or []),
        "excluded": len(report.get("excluded") or []),
        "shortfall": report.get("shortfall"),
        "search_approximate": (report.get("search") or {}).get("approximate"),
        "engine": report["engine"], "ruleset": report["ruleset"], "reference": report["reference"],
        "weather_name": report.get("weather_name"), "trainers": report.get("trainers"),
    }
    if kind == "raid":
        out["mega_comparison"] = report.get("mega_comparison")
        out["dodge_comparison"] = report.get("dodge_comparison")
        shadow = report.get("shadow")
        out["shadow"] = None if not shadow else {k: shadow.get(k) for k in (
            "enrage_at", "attack", "defense", "enraged_s", "enraged_runs", "runs", "comparison")}
    return out

"""Game reference data kept in the local data folder.

Files are downloaded only by `python -m boxscan reference update`. Each update goes into its
own versioned folder with the raw files, their source URL, fetch time and SHA-256, and a
compact `reference.json` that analysis reads. The previous version stays in place if a
download or validation fails. The data is not committed to Git.

Sources and precedence:
- Base stats and CPM: PokeMiners game master, which lists whole levels only. Half levels below
  40 use sqrt((cpm(l)^2 + cpm(l+1)^2) / 2); from 40 they use the midpoint, as PoGo API's table
  does. Both rules are cross-checked against PoGo API up to level 45; 45.5–49.5 follow the
  midpoint rule without an outside check.
- PoGo API stats fill species the game master lacks. Its CPM table (to level 45) is only a check.
- Korean species names: PokeAPI CSV.
- Moves: per-species learnsets (regular, elite, shadow-only) and PvE power come from the game
  master. Game master move IDs are joined to PokeAPI's English move names, then to Korean.
"""
import csv
import hashlib
import io
import json
import math
import re
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

SOURCES = {
    "game_master.json": "https://raw.githubusercontent.com/PokeMiners/game_masters/master/latest/latest.json",
    "pokemon_stats.json": "https://pogoapi.net/api/v1/pokemon_stats.json",
    "cp_multiplier.json": "https://pogoapi.net/api/v1/cp_multiplier.json",
    "pokemon_species_names.csv":
        "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/pokemon_species_names.csv",
    "move_names.csv": "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/move_names.csv",
}
DERIVED = "reference.json"
KOREAN = "3"  # PokeAPI local_language_id for Korean
ENGLISH = "9"
MOVE_TEMPLATE = re.compile(r"^V(\d{4})_MOVE_(.+)$")
MOVE_ALIASES = {"VICEGRIP": "VISEGRIP"}  # renamed in the main series
LEARNSET_KEYS = {"quickMoves": "fast", "cinematicMoves": "charged",
                 "eliteQuickMove": "elite_fast", "eliteCinematicMove": "elite_charged"}
MAX_BYTES = 64 * 1024 * 1024
MAX_LEVEL = 55.0
CPM_TOLERANCE = 1e-6
USER_AGENT = "boxscan-reference/0.3"
TEMPLATE = re.compile(r"^V(\d{4})_POKEMON_")


@dataclass(frozen=True)
class BaseStats:
    pokemon_id: int
    name: str
    form: str
    attack: int
    defense: int
    stamina: int


@dataclass
class Reference:
    version: str
    fetched_at: str
    stats: dict[int, list[BaseStats]]  # pokemon id -> distinct forms, regular form first
    cpm: dict[float, float]  # level -> CP multiplier
    korean_names: dict[str, int]  # Korean species name -> pokemon id
    notes: dict | None = None
    move_names: dict[str, str] | None = None  # game master move id -> Korean name
    move_power: dict[str, float] | None = None  # game master move id -> PvE power
    learnsets: dict[str, dict[str, list[str]]] | None = None  # "id|form" -> role -> move ids
    # Mega / primal stats: pokemon id -> {"MEGA" | "MEGA_X" | "MEGA_Y" | "PRIMAL": stats}
    temp_stats: dict[int, dict[str, BaseStats]] | None = None
    # Fusion and other form changes from the regular form: pokemon id -> list of
    # {"target": form, "type": "FUSE" | "CHANGE", "component": Korean name | None,
    #  "existing_moves": [...], "replacement_moves": [...]}
    form_changes: dict[int, list[dict]] | None = None

    def forms(self, korean_name: str) -> list[BaseStats]:
        pokemon_id = self.korean_names.get(korean_name.strip())
        return self.stats.get(pokemon_id, []) if pokemon_id else []


def root(data_dir: Path) -> Path:
    return data_dir / "reference"


def parse(files: dict[str, bytes], version: str = "", fetched_at: str = "") -> Reference:
    """Validate and combine the raw files. Raises ValueError when the data looks wrong."""
    try:
        master = json.loads(files["game_master.json"])
        stats, learnsets = _master_stats(master)
        temp_stats, form_changes, enum_ids = _master_extras(master)
        numbers, move_power = _master_moves(master)
        move_names, unmatched = _korean_moves(
            files["move_names.csv"], numbers, learnsets, move_power)
        cpm = _master_cpm(master)
        fallback = 0
        for item in json.loads(files["pokemon_stats.json"]):
            pokemon_id = int(item["pokemon_id"])
            if pokemon_id in stats:
                continue
            fallback += 1
            _add(stats, BaseStats(pokemon_id, str(item["pokemon_name"]), str(item["form"]),
                                  int(item["base_attack"]), int(item["base_defense"]),
                                  int(item["base_stamina"])))
        check = {float(item["level"]): float(item["multiplier"])
                 for item in json.loads(files["cp_multiplier.json"])}
        names = {}
        rows = csv.DictReader(io.StringIO(files["pokemon_species_names.csv"].decode("utf-8")))
        for row in rows:
            if row["local_language_id"] == KOREAN and row["name"]:
                names[row["name"].strip()] = int(row["pokemon_species_id"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"기준 데이터 형식이 예상과 다릅니다: {error}") from error
    if len(stats) < 100 or len(names) < 100 or not {1.0, 40.0, 50.0} <= cpm.keys():
        raise ValueError("기준 데이터가 너무 적거나 필요한 레벨이 없습니다.")
    shared = sorted(cpm.keys() & check.keys())
    worst = max((abs(cpm[level] - check[level]) for level in shared), default=math.inf)
    if len(shared) < 10 or worst > CPM_TOLERANCE:
        raise ValueError(f"게임 마스터 CPM이 PoGo API 값과 맞지 않습니다 (최대 차이 {worst}).")
    for forms in stats.values():
        forms.sort(key=lambda form: form.form != "Normal")
    if len(move_names) < 100:
        raise ValueError("기술 이름을 거의 연결하지 못했습니다.")
    kept = {f"{form.pokemon_id}|{form.form}" for forms in stats.values() for form in forms}
    learnsets = {key: _resolve(value, numbers) for key, value in learnsets.items() if key in kept}
    notes = {"cpm_checked_levels": len(shared), "cpm_max_difference": worst,
             "stats_from_pogoapi": fallback, "moves_without_korean_name": unmatched}
    korean_by_id = {pokemon_id: name for name, pokemon_id in names.items()}
    for changes in form_changes.values():
        for change in changes:
            component = change.pop("component_enum", None)
            change["component"] = korean_by_id.get(enum_ids.get(component)) if component else None
            for key in ("existing_moves", "replacement_moves"):
                change[key] = sorted({numbers.get(m, m) for m in change[key]})
    return Reference(version, fetched_at, stats, cpm, names, notes, move_names, move_power, learnsets,
                     temp_stats, form_changes)


def _form_code(name: str, form: str | None) -> str:
    form = str(form or "Normal")
    form = form.removeprefix(f"{name}_") if form.startswith(f"{name}_") else form
    return "Normal" if form.upper() == "NORMAL" else form.title()


def _master_extras(master: list[dict]) -> tuple[dict, dict, dict]:
    """Mega/primal stats and form changes, read from each species' regular template."""
    temp_stats: dict[int, dict[str, BaseStats]] = {}
    form_changes: dict[int, list[dict]] = {}
    enum_ids: dict[str, int] = {}
    form_stats: dict[tuple[int, str], list[int]] = {}
    for item in master:
        match = TEMPLATE.match(item["templateId"])
        settings = item.get("data", {}).get("pokemonSettings")
        if not match or not settings:
            continue
        pokemon_id, name = int(match[1]), settings["pokemonId"]
        enum_ids.setdefault(name, pokemon_id)
        form = _form_code(name, settings.get("form"))
        base = settings.get("stats") or {}
        if all(k in base for k in ("baseAttack", "baseDefense", "baseStamina")):
            form_stats.setdefault((pokemon_id, form), [
                int(base["baseAttack"]), int(base["baseDefense"]), int(base["baseStamina"])])
        if form != "Normal":
            continue
        for override in settings.get("tempEvoOverrides") or []:
            base = override.get("stats") or {}
            kind = str(override.get("tempEvoId", "")).removeprefix("TEMP_EVOLUTION_")
            if kind and all(k in base for k in ("baseAttack", "baseDefense", "baseStamina")):
                temp_stats.setdefault(pokemon_id, {})[kind] = BaseStats(
                    pokemon_id, name.title(), kind.title(), int(base["baseAttack"]),
                    int(base["baseDefense"]), int(base["baseStamina"]))
        if pokemon_id in form_changes:
            continue
        changes, seen = [], set()
        for change in settings.get("formChange") or []:
            component = change.get("componentPokemonSettings") or {}
            if component.get("formChangeType") == "UNFUSE":
                continue
            existing, replacement = set(), set()
            for slots in (change.get("moveReassignment") or {}).values():
                for slot in slots:
                    existing.update(str(m) for m in slot.get("existingMoves") or [])
                    replacement.update(str(m) for m in slot.get("replacementMoves") or [])
            for target in change.get("availableForm") or []:
                key = (_form_code(name, target), tuple(sorted(replacement)))
                if key in seen:
                    continue
                seen.add(key)
                changes.append({
                    "target": _form_code(name, target),
                    "type": "FUSE" if component.get("formChangeType") == "FUSE" else "CHANGE",
                    "component_enum": component.get("pokedexId"),
                    "existing_moves": list(existing),
                    "replacement_moves": list(replacement),
                })
        if changes:
            form_changes[pokemon_id] = changes
    # Each target keeps its own stats; identical stat lines (블랙/화이트큐레무) are merged in `stats`.
    for pokemon_id, changes in form_changes.items():
        for change in changes:
            change["stats"] = form_stats.get((pokemon_id, change["target"]))
    return temp_stats, form_changes, enum_ids


def _resolve(learnset: dict[str, list[str]], numbers: dict[str, str]) -> dict[str, list[str]]:
    return {role: sorted({numbers.get(move, move) for move in moves}) for role, moves in learnset.items()}


def _master_moves(master: list[dict]) -> tuple[dict[str, str], dict[str, float]]:
    """Map numeric move ids to names and read PvE power from V####_MOVE_* templates."""
    numbers, power = {}, {}
    for item in master:
        match = MOVE_TEMPLATE.match(item["templateId"])
        settings = item.get("data", {}).get("moveSettings")
        if match and settings is not None:
            numbers[str(int(match[1]))] = match[2]
            power[match[2]] = float(settings.get("power", 0.0))
    return numbers, power


def _move_key(text: str) -> str:
    key = re.sub(r"[^A-Z0-9]", "", text.upper())
    return MOVE_ALIASES.get(key, key)


def _korean_moves(raw: bytes, numbers: dict[str, str], learnsets: dict, power: dict) -> tuple[dict, list]:
    """Join game master move ids to Korean names through PokeAPI's English names."""
    by_move: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
        by_move.setdefault(row["move_id"], {})[row["local_language_id"]] = row["name"].strip()
    english = {_move_key(names[ENGLISH]): names.get(KOREAN)
               for names in by_move.values() if ENGLISH in names}
    moves = set(power)
    for learnset in learnsets.values():
        moves.update(numbers.get(m, m) for ms in learnset.values() for m in ms)
    korean, unmatched = {}, []
    for move in sorted(moves):
        base = move.removesuffix("_FAST")
        parts = base.split("_")
        plus = ""
        while parts and parts[-1] == "PLUS":  # Aeroblast+ / Sacred Fire++
            plus += "+"
            parts.pop()
        # Typed variants such as WEATHER_BALL_FIRE or TECHNO_BLAST_BURN share the base name.
        while parts and _move_key("_".join(parts)) not in english:
            parts.pop()
        name = english.get(_move_key("_".join(parts))) if parts else None
        if name:
            korean[move] = name + plus
        else:
            unmatched.append(move)
    return korean, unmatched


def _add(stats: dict[int, list[BaseStats]], entry: BaseStats) -> None:
    """Keep one entry per distinct stat line; costumes and shadow copies share the regular stats."""
    forms = stats.setdefault(entry.pokemon_id, [])
    key = (entry.attack, entry.defense, entry.stamina)
    for index, existing in enumerate(forms):
        if (existing.attack, existing.defense, existing.stamina) == key:
            if entry.form == "Normal" and existing.form != "Normal":
                forms[index] = entry
            return
    forms.append(entry)


def _master_stats(master: list[dict]) -> tuple[dict[int, list[BaseStats]], dict]:
    stats: dict[int, list[BaseStats]] = {}
    learnsets: dict[str, dict[str, set[str]]] = {}
    for item in master:
        match = TEMPLATE.match(item["templateId"])
        settings = item.get("data", {}).get("pokemonSettings")
        if not match or not settings or "stats" not in settings:
            continue
        base = settings["stats"]
        if not all(key in base for key in ("baseAttack", "baseDefense", "baseStamina")):
            continue
        name = settings["pokemonId"]
        form = _form_code(name, settings.get("form"))
        _add(stats, BaseStats(int(match[1]), name.title(), form, int(base["baseAttack"]),
                              int(base["baseDefense"]), int(base["baseStamina"])))
        learnset = learnsets.setdefault(f"{int(match[1])}|{form}", {})
        for key, role in LEARNSET_KEYS.items():
            learnset.setdefault(role, set()).update(str(m) for m in settings.get(key) or [])
        shadow = settings.get("shadow") or {}
        if shadow.get("shadowChargeMove"):
            learnset.setdefault("shadow", set()).add(str(shadow["shadowChargeMove"]))
        if shadow.get("purifiedChargeMove"):
            learnset.setdefault("purified", set()).add(str(shadow["purifiedChargeMove"]))
    return stats, learnsets


def _master_cpm(master: list[dict]) -> dict[float, float]:
    (levels,) = [item["data"]["playerLevel"]["cpMultiplier"] for item in master
                 if item["templateId"] == "PLAYER_LEVEL_SETTINGS"]
    whole = {float(index + 1): float(value) for index, value in enumerate(levels)
             if index + 1 <= MAX_LEVEL}
    cpm = dict(whole)
    for level in list(whole):
        if level + 1 in whole:
            low, high = whole[level], whole[level + 1]
            cpm[level + 0.5] = (math.sqrt((low ** 2 + high ** 2) / 2) if level < 40
                                else (low + high) / 2)
    return dict(sorted(cpm.items()))


def to_json(reference: Reference) -> dict:
    return {
        "version": reference.version,
        "fetched_at": reference.fetched_at,
        "notes": reference.notes,
        "cpm": {str(level): value for level, value in reference.cpm.items()},
        "korean_names": reference.korean_names,
        "stats": [asdict(form) for forms in reference.stats.values() for form in forms],
        "move_names": reference.move_names,
        "move_power": reference.move_power,
        "learnsets": reference.learnsets,
        "temp_stats": [{"kind": kind, **asdict(stats)}
                       for forms in (reference.temp_stats or {}).values() for kind, stats in forms.items()],
        "form_changes": {str(k): v for k, v in (reference.form_changes or {}).items()},
    }


def from_json(data: dict) -> Reference:
    stats: dict[int, list[BaseStats]] = {}
    for item in data["stats"]:
        stats.setdefault(item["pokemon_id"], []).append(BaseStats(**item))
    temp_stats: dict[int, dict[str, BaseStats]] = {}
    for item in data.get("temp_stats") or []:
        kind = item.pop("kind")
        temp_stats.setdefault(item["pokemon_id"], {})[kind] = BaseStats(**item)
    return Reference(data["version"], data["fetched_at"], stats,
                     {float(level): value for level, value in data["cpm"].items()},
                     data["korean_names"], data.get("notes"), data.get("move_names"),
                     data.get("move_power"), data.get("learnsets"), temp_stats,
                     {int(k): v for k, v in (data.get("form_changes") or {}).items()})


def rebuild(data_dir: Path) -> dict:
    """Re-derive reference.json from the raw files of the current version, without downloading."""
    pointer = root(data_dir) / "current"
    if not pointer.is_file():
        raise ValueError("기준 데이터가 없습니다. `reference update`를 실행하세요.")
    folder = root(data_dir) / pointer.read_text(encoding="utf-8").strip()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    missing = [name for name in SOURCES if not (folder / name).is_file()]
    if missing:
        raise ValueError(f"원본 파일이 없어 다시 만들 수 없습니다: {', '.join(missing)}. `reference update`를 실행하세요.")
    parsed = parse({name: (folder / name).read_bytes() for name in SOURCES},
                   manifest["version"], manifest["fetched_at"])
    partial = folder / f"{DERIVED}.partial"
    partial.write_text(json.dumps(to_json(parsed), ensure_ascii=False), encoding="utf-8")
    partial.replace(folder / DERIVED)
    return manifest


def load(data_dir: Path) -> Reference | None:
    pointer = root(data_dir) / "current"
    if not pointer.is_file():
        return None
    folder = root(data_dir) / pointer.read_text(encoding="utf-8").strip()
    return from_json(json.loads((folder / DERIVED).read_text(encoding="utf-8")))


def update(data_dir: Path, fetch=None) -> dict:
    """Download all sources, validate them, then switch `current` to the new version."""
    fetch = fetch or _fetch
    files = {name: fetch(url) for name, url in SOURCES.items()}
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    version = hashlib.sha256("".join(digests[n] for n in sorted(digests)).encode()).hexdigest()[:12]
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parsed = parse(files, version, fetched_at)
    folder = root(data_dir) / version
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    (folder / DERIVED).write_text(json.dumps(to_json(parsed), ensure_ascii=False), encoding="utf-8")
    manifest = {
        "version": version,
        "fetched_at": fetched_at,
        "notes": parsed.notes,
        "files": {name: {"url": SOURCES[name], "sha256": digests[name], "bytes": len(files[name])}
                  for name in SOURCES},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    partial = root(data_dir) / "current.partial"
    partial.write_text(version, encoding="utf-8")
    partial.replace(root(data_dir) / "current")
    return manifest


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"예상보다 큰 파일입니다: {url}")
    return data

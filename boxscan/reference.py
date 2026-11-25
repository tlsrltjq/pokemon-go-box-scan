"""Game reference data kept in the local data folder.

Files are downloaded only by `python -m boxscan reference update`. Each update goes into its
own versioned folder with the raw files, their source URL, fetch time and SHA-256, and a
compact `reference.json` that analysis reads. The previous version stays in place if a
download or validation fails. The data is not committed to Git.

Sources and precedence:
- Base stats and CPM: game master (alexelgt/game_masters mirror), which lists whole levels only. Half levels below
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
    # alexelgt/game_masters is updated with the game; PokeMiners stopped after 2026-08-29.
    "game_master.json": "https://raw.githubusercontent.com/alexelgt/game_masters/master/GAME_MASTER.json",
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
# Additional charged attacks of mega evolutions ("마지막일침+"), one template per species and state.
MEGA_MOVE_TEMPLATE = re.compile(r"^VM_MOVE_TEMP_EVOLUTION_(MEGA(?:_X|_Y)?|PRIMAL)_V(\d{4})_POKEMON_")
MOVE_ALIASES = {"VICEGRIP": "VISEGRIP"}  # renamed in the main series
VFX_ALIASES = {"MYST_FIRE": "MYSTICAL_FIRE"}  # mega move animation names that differ from move ids
LEARNSET_KEYS = {"quickMoves": "fast", "cinematicMoves": "charged",
                 "eliteQuickMove": "elite_fast", "eliteCinematicMove": "elite_charged"}
MAX_BYTES = 64 * 1024 * 1024
MAX_LEVEL = 55.0
CPM_TOLERANCE = 1e-6
USER_AGENT = "boxscan-reference/0.3"
TEMPLATE = re.compile(r"^V(\d{4})_POKEMON_")
UPGRADE_OVERRIDE = re.compile(r"^POKEMON_UPGRADE_OVERRIDE_SETTINGS_V(\d{4})_POKEMON_")


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
    # pokemon id -> {"MEGA" | "MEGA_X" | ...: {"move_id", "base_move", "name", "power"}}
    mega_moves: dict[int, dict[str, dict]] | None = None
    # Battle data for the raid engine (PvE values from the game master)
    types: dict[str, list[str]] | None = None  # "id|form" or "id|MEGA" -> ["GROUND", "FIRE"]
    move_stats: dict[str, dict] | None = None  # move id -> type, power, energy, duration, damage window
    type_chart: dict[str, dict[str, float]] | None = None  # attacking type -> defending type -> scalar
    weather: dict[str, list[str]] | None = None  # weather -> boosted types
    battle: dict | None = None  # BATTLE_SETTINGS subset
    # Max battles: VN_BM_* max moves -> {"type", "kind": attack|gmax|guard|spirit, "name"}
    max_moves: dict[str, dict] | None = None
    max_move_by_type: dict[str, str] | None = None  # fast move type -> Max Attack move id
    species_max_moves: dict[str, str] | None = None  # "id|form" ("*" = any form) -> G-Max or special move id
    # Mega / primal raid boosts: {"same_type", "different_type", "special_move": [by mega level],
    #  "types": {pokemon id: boosted types} for primal-style boosts that differ from the Pokémon's types}
    temp_boosts: dict | None = None
    # Candy family: pokemon id -> id of the Pokémon the candy is named after (마기라스 -> 애버라스)
    families: dict[int, int] | None = None
    # Power-up cost per whole level from POKEMON_UPGRADE_SETTINGS: {"stardust": [...], "candy": [...],
    #  "xl": [...], "xl_from": 40, "shadow": 1.2, "purified": 0.9, "overrides": {"890": {"candy", "xl"}}};
    #  index = level - 1 (xl: level - xl_from). Overrides are species with their own candy cost (무한다이노).
    upgrades: dict | None = None

    def candy_name(self, korean_name: str | None) -> str | None:
        """The species whose name the candy carries: 마기라스 -> 애버라스."""
        pokemon_id = self.korean_names.get((korean_name or "").strip())
        base = (self.families or {}).get(pokemon_id, pokemon_id) if pokemon_id else None
        if base is None:
            return None
        return next((name for name, number in self.korean_names.items() if number == base), None)

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
    mega_moves = _mega_moves(master, move_names)
    notes["mega_moves"] = sum(len(v) for v in mega_moves.values())
    types, move_stats, type_chart, weather, battle = _battle_data(master)
    max_moves, max_move_by_type, species_max_moves = _max_data(master, files["move_names.csv"])
    temp_boosts = _temp_boosts(master, enum_ids)
    families = _families(master, enum_ids)
    upgrades = _upgrades(master)
    if len(type_chart) != 18 or not move_stats or "energyDeltaPerHealthLost" not in battle:
        raise ValueError("전투 계산용 데이터(상성·기술·전투 설정)를 읽지 못했습니다.")
    return Reference(version, fetched_at, stats, cpm, names, notes, move_names, move_power, learnsets,
                     temp_stats, form_changes, mega_moves, types, move_stats, type_chart, weather, battle,
                     max_moves, max_move_by_type, species_max_moves, temp_boosts, families, upgrades)


def _upgrades(master: list[dict]) -> dict | None:
    """The power-up cost table. The screen shows the next power-up's stardust and candy, which fixes
    the level to a range of two levels when the CP number is hidden behind the Pokémon."""
    table, overrides = None, {}
    for item in master:
        data = item.get("data", {}).get("pokemonUpgrades") or {}
        if item["templateId"] == "POKEMON_UPGRADE_SETTINGS":
            if not data.get("stardustCost") or not data.get("candyCost"):
                return None
            table = {"stardust": [int(v) for v in data["stardustCost"]], "candy": [int(v) for v in data["candyCost"]],
                     "xl": [int(v) for v in data.get("xlCandyCost") or []],
                     "xl_from": float(data.get("xlCandyMinPokemonLevel", 40)),
                     "shadow": float(data.get("shadowStardustMultiplier", 1.2)),
                     "purified": float(data.get("purifiedStardustMultiplier", 0.9))}
        elif (match := UPGRADE_OVERRIDE.match(item["templateId"])) and data.get("candyCost"):
            override = overrides[str(int(match[1]))] = {"candy": [int(v) for v in data["candyCost"]]}
            if data.get("xlCandyCost"):
                override["xl"] = [int(v) for v in data["xlCandyCost"]]
    if table:
        table["overrides"] = overrides
    return table


def _families(master: list[dict], enum_ids: dict[str, int]) -> dict[int, int]:
    """familyId FAMILY_LARVITAR on 마기라스 -> the id of LARVITAR (애버라스), whose name the candy carries."""
    families: dict[int, int] = {}
    for item in master:
        match = TEMPLATE.match(item["templateId"])
        settings = item.get("data", {}).get("pokemonSettings")
        family = (settings or {}).get("familyId")
        if match and family:
            base = enum_ids.get(str(family).removeprefix("FAMILY_"))
            if base:
                families.setdefault(int(match[1]), base)
    return families


def _temp_boosts(master: list[dict], enum_ids: dict[str, int]) -> dict:
    """Raid attack boosts of an active mega / primal (MEGA_EVO_SETTINGS, PRIMAL_EVO_SETTINGS)."""
    found: dict = {"types": {}}
    for item in master:
        data = item.get("data", {})
        if item["templateId"] == "MEGA_EVO_SETTINGS":
            settings = data.get("megaEvoSettings", {})
            found["same_type"] = float(settings["attackBoostFromMegaSameType"])
            found["different_type"] = float(settings["attackBoostFromMegaDifferentType"])
            found["special_move"] = [float(b.get("gymsAndRaidsMultiplier", 1.0))
                                     for b in settings.get("tempEvoSpecialAttackBoosts") or []]
        elif item["templateId"] == "PRIMAL_EVO_SETTINGS":
            for boost in data.get("primalEvoSettings", {}).get("typeBoosts") or []:
                pokemon_id = enum_ids.get(boost.get("pokemonId"))
                if pokemon_id:
                    found["types"][str(pokemon_id)] = sorted(_type(t) for t in boost.get("boostType") or [])
    if "same_type" not in found:
        raise ValueError("메가진화 보너스 설정(MEGA_EVO_SETTINGS)을 찾지 못했습니다.")
    return found


# Exclusive Max Attacks missing from the name list, as the Korean client shows them (user-checked).
CLIENT_MAX_NAMES = {"MAX_BEHEMOTH_BLADE": "거수참", "MAX_BEHEMOTH_BASH": "거수탄", "MAX_DYNAMAX_CANNON": "다이맥스포"}
# Max Guard keeps the main-series name 다이월; Max Spirit exists only in GO and is shown as 다이리커버리.
MAX_KINDS = {"MAX_SHIELD": ("guard", "다이월"), "MAX_HEAL": ("spirit", "다이리커버리")}


def _max_data(master: list[dict], move_names_csv: bytes) -> tuple[dict, dict, dict]:
    """Max moves (VN_BM_*), the Max Attack for each fast move type, and each Gigantamax species'
    own move (SOURDOUGH_MOVE_MAPPING_SETTINGS)."""
    korean = {}
    for row in csv.DictReader(io.StringIO(move_names_csv.decode("utf-8"))):
        korean.setdefault(row["move_id"], {})[row["local_language_id"]] = row["name"].strip()
    english = {_move_key(n[ENGLISH]): n.get(KOREAN) for n in korean.values() if ENGLISH in n}
    moves, by_type, species, enum_ids, mappings = {}, {}, {}, {}, []
    for item in master:
        template, data = item["templateId"], item.get("data", {})
        match = TEMPLATE.match(template)
        if match and data.get("pokemonSettings"):
            enum_ids.setdefault(data["pokemonSettings"]["pokemonId"], int(match[1]))
        settings = data.get("moveSettings")
        if template.startswith("VN_BM_") and settings:
            vfx = str(settings.get("vfxName") or "").upper()
            if vfx in MAX_KINDS:
                kind, name = MAX_KINDS[vfx]
            else:
                kind = "gmax" if vfx.startswith("GMAX_") else "attack"
                name = english.get(_move_key(vfx)) or CLIENT_MAX_NAMES.get(vfx)
            moves[template] = {"type": _type(settings.get("pokemonType")), "kind": kind, "name": name, "vfx": vfx.lower()}
        if "breadMoveMappings" in data:
            by_type = {_type(m["type"]): m["move"] for m in data["breadMoveMappings"].get("mappings", [])}
        if "sourdoughMoveMappingSettings" in data:
            mappings = data["sourdoughMoveMappingSettings"].get("mappings", [])
    for mapping in mappings:
        pokemon_id = enum_ids.get(mapping.get("pokemonId"))
        if pokemon_id and mapping.get("move") in moves:
            form = _form_code(mapping["pokemonId"], mapping["form"]) if mapping.get("form") else "*"
            species[f"{pokemon_id}|{form}"] = mapping["move"]
    return moves, by_type, species


# Order of POKEMON_TYPE_* in the game data; typeEffective.attackScalar follows it.
TYPE_ORDER = ["NORMAL", "FIGHTING", "FLYING", "POISON", "GROUND", "ROCK", "BUG", "GHOST", "STEEL",
              "FIRE", "WATER", "GRASS", "ELECTRIC", "PSYCHIC", "ICE", "DRAGON", "DARK", "FAIRY"]
BATTLE_KEYS = ("sameTypeAttackBonusMultiplier", "maximumEnergy", "energyDeltaPerHealthLost",
               "swapDurationMs", "enemyAttackInterval", "bossEnergyRegenerationPerHealthLost",
               "shadowPokemonAttackBonusMultiplier", "shadowPokemonDefenseBonusMultiplier")


def _type(value) -> str | None:
    return str(value).removeprefix("POKEMON_TYPE_") if value else None


def _battle_data(master: list[dict]):
    types, move_stats, chart, weather, battle = {}, {}, {}, {}, {}
    for item in master:
        template, data = item["templateId"], item.get("data", {})
        match = TEMPLATE.match(template)
        settings = data.get("pokemonSettings")
        if match and settings:
            form = _form_code(settings["pokemonId"], settings.get("form"))
            key = f"{int(match[1])}|{form}"
            if key not in types:
                types[key] = [t for t in (_type(settings.get("type")), _type(settings.get("type2"))) if t]
            if form == "Normal":
                for override in settings.get("tempEvoOverrides") or []:
                    kind = str(override.get("tempEvoId", "")).removeprefix("TEMP_EVOLUTION_")
                    found = [t for t in (_type(override.get("typeOverride1")), _type(override.get("typeOverride2"))) if t]
                    if kind and found:
                        types[f"{int(match[1])}|{kind}"] = found
        move = data.get("moveSettings")
        name = MOVE_TEMPLATE.match(template) or MEGA_MOVE_TEMPLATE.match(template)
        if move and name:
            move_id = template if template.startswith("VM_") else name[2]
            move_stats[move_id] = {
                "type": _type(move.get("pokemonType")),
                "power": float(move.get("power", 0.0)),
                "energy": int(move.get("energyDelta", 0)),
                "duration_ms": int(move.get("durationMs", 0)),
                "damage_start_ms": int(move.get("damageWindowStartMs", 0)),
            }
        effective = data.get("typeEffective")
        if effective:
            chart[_type(effective["attackType"])] = dict(zip(TYPE_ORDER, map(float, effective["attackScalar"])))
        affinity = data.get("weatherAffinities")
        if affinity:
            weather[affinity["weatherCondition"]] = [_type(t) for t in affinity.get("pokemonType", [])]
        if "battleSettings" in data:
            battle = {k: data["battleSettings"][k] for k in BATTLE_KEYS if k in data["battleSettings"]}
    return types, move_stats, chart, weather, battle


def _mega_moves(master: list[dict], move_names: dict[str, str]) -> dict[int, dict[str, dict]]:
    """Map VM_MOVE_TEMP_EVOLUTION_* templates to species. The Korean name is the base move's name
    (from the animation name, e.g. fell_stinger -> FELL_STINGER -> 마지막일침) followed by "+"."""
    found: dict[int, dict[str, dict]] = {}
    for item in master:
        match = MEGA_MOVE_TEMPLATE.match(item["templateId"])
        settings = item.get("data", {}).get("moveSettings")
        if not match or settings is None:
            continue
        base = str(settings.get("vfxName") or "").upper()
        base = VFX_ALIASES.get(base, base)
        name = move_names.get(base) or move_names.get(f"{base}_FAST")
        found.setdefault(int(match[2]), {})[match[1]] = {
            "move_id": item["templateId"], "base_move": base or None,
            "name": f"{name}+" if name else None, "power": float(settings.get("power", 0.0)),
        }
    return found


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
        # A move the Pokémon must know to mega evolve (레쿠쟈: 화룡점정). The moves screen shows it
        # locked even when the Pokémon has not learned it, so OCR must not take it as a known move.
        for branch in settings.get("evolutionBranch") or []:
            if branch.get("evolutionMoveRequirement"):
                learnset.setdefault("mega_required", set()).add(str(branch["evolutionMoveRequirement"]))
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
        "mega_moves": {str(k): v for k, v in (reference.mega_moves or {}).items()},
        "types": reference.types,
        "move_stats": reference.move_stats,
        "type_chart": reference.type_chart,
        "weather": reference.weather,
        "battle": reference.battle,
        "max_moves": reference.max_moves,
        "max_move_by_type": reference.max_move_by_type,
        "species_max_moves": reference.species_max_moves,
        "temp_boosts": reference.temp_boosts,
        "families": {str(k): v for k, v in (reference.families or {}).items()},
        "upgrades": reference.upgrades,
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
                     {int(k): v for k, v in (data.get("form_changes") or {}).items()},
                     {int(k): v for k, v in (data.get("mega_moves") or {}).items()},
                     data.get("types"), data.get("move_stats"), data.get("type_chart"),
                     data.get("weather"), data.get("battle"), data.get("max_moves"),
                     data.get("max_move_by_type"), data.get("species_max_moves"), data.get("temp_boosts"),
                     {int(k): v for k, v in (data.get("families") or {}).items()}, data.get("upgrades"))


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

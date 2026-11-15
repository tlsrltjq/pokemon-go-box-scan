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
}
DERIVED = "reference.json"
KOREAN = "3"  # PokeAPI local_language_id for Korean
MAX_BYTES = 64 * 1024 * 1024
MAX_LEVEL = 55.0
CPM_TOLERANCE = 1e-6
USER_AGENT = "boxscan-reference/0.2"
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

    def forms(self, korean_name: str) -> list[BaseStats]:
        pokemon_id = self.korean_names.get(korean_name.strip())
        return self.stats.get(pokemon_id, []) if pokemon_id else []


def root(data_dir: Path) -> Path:
    return data_dir / "reference"


def parse(files: dict[str, bytes], version: str = "", fetched_at: str = "") -> Reference:
    """Validate and combine the raw files. Raises ValueError when the data looks wrong."""
    try:
        master = json.loads(files["game_master.json"])
        stats = _master_stats(master)
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
    notes = {"cpm_checked_levels": len(shared), "cpm_max_difference": worst,
             "stats_from_pogoapi": fallback}
    return Reference(version, fetched_at, stats, cpm, names, notes)


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


def _master_stats(master: list[dict]) -> dict[int, list[BaseStats]]:
    stats: dict[int, list[BaseStats]] = {}
    for item in master:
        match = TEMPLATE.match(item["templateId"])
        settings = item.get("data", {}).get("pokemonSettings")
        if not match or not settings or "stats" not in settings:
            continue
        base = settings["stats"]
        if not all(key in base for key in ("baseAttack", "baseDefense", "baseStamina")):
            continue
        name = settings["pokemonId"]
        form = str(settings.get("form") or "Normal")
        form = form.removeprefix(f"{name}_") if form.startswith(f"{name}_") else form
        form = "Normal" if form.upper() == "NORMAL" else form.title()
        _add(stats, BaseStats(int(match[1]), name.title(), form, int(base["baseAttack"]),
                              int(base["baseDefense"]), int(base["baseStamina"])))
    return stats


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
    }


def from_json(data: dict) -> Reference:
    stats: dict[int, list[BaseStats]] = {}
    for item in data["stats"]:
        stats.setdefault(item["pokemon_id"], []).append(BaseStats(**item))
    return Reference(data["version"], data["fetched_at"], stats,
                     {float(level): value for level, value in data["cpm"].items()},
                     data["korean_names"], data.get("notes"))


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

"""Game reference data kept in the local data folder.

Files are downloaded only by `python -m boxscan reference update`. Each update goes into its
own versioned folder with the source URL, fetch time and SHA-256 of every file. The previous
version stays in place if a download or validation fails. The data is not committed to Git.
"""
import csv
import hashlib
import io
import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SOURCES = {
    "pokemon_stats.json": "https://pogoapi.net/api/v1/pokemon_stats.json",
    "cp_multiplier.json": "https://pogoapi.net/api/v1/cp_multiplier.json",
    "pokemon_species_names.csv":
        "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/pokemon_species_names.csv",
}
KOREAN = "3"  # PokeAPI local_language_id for Korean
MAX_BYTES = 5 * 1024 * 1024
USER_AGENT = "boxscan-reference/0.1"


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
    stats: dict[int, list[BaseStats]]  # pokemon id -> forms
    cpm: dict[float, float]  # level -> CP multiplier
    korean_names: dict[str, int]  # Korean species name -> pokemon id

    def forms(self, korean_name: str) -> list[BaseStats]:
        pokemon_id = self.korean_names.get(korean_name.strip())
        return self.stats.get(pokemon_id, []) if pokemon_id else []


def root(data_dir: Path) -> Path:
    return data_dir / "reference"


def parse(files: dict[str, bytes], version: str = "", fetched_at: str = "") -> Reference:
    """Validate and parse the raw files. Raises ValueError when the format looks wrong."""
    try:
        stats: dict[int, list[BaseStats]] = {}
        for item in json.loads(files["pokemon_stats.json"]):
            entry = BaseStats(int(item["pokemon_id"]), str(item["pokemon_name"]), str(item["form"]),
                              int(item["base_attack"]), int(item["base_defense"]),
                              int(item["base_stamina"]))
            stats.setdefault(entry.pokemon_id, []).append(entry)
        cpm = {float(item["level"]): float(item["multiplier"])
               for item in json.loads(files["cp_multiplier.json"])}
        names = {}
        rows = csv.DictReader(io.StringIO(files["pokemon_species_names.csv"].decode("utf-8")))
        for row in rows:
            if row["local_language_id"] == KOREAN and row["name"]:
                names[row["name"].strip()] = int(row["pokemon_species_id"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"기준 데이터 형식이 예상과 다릅니다: {error}") from error
    # PoGo API's CPM table currently stops at level 45; level checks report that limit.
    if len(stats) < 100 or not {1.0, 40.0} <= cpm.keys() or len(names) < 100:
        raise ValueError("기준 데이터가 너무 적거나 필요한 레벨이 없습니다.")
    return Reference(version, fetched_at, stats, cpm, names)


def load(data_dir: Path) -> Reference | None:
    base = root(data_dir)
    pointer = base / "current"
    if not pointer.is_file():
        return None
    folder = base / pointer.read_text(encoding="utf-8").strip()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = {name: (folder / name).read_bytes() for name in SOURCES}
    return parse(files, manifest["version"], manifest["fetched_at"])


def update(data_dir: Path, fetch=None) -> dict:
    """Download all sources, validate them, then switch `current` to the new version."""
    fetch = fetch or _fetch
    files = {name: fetch(url) for name, url in SOURCES.items()}
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    version = hashlib.sha256("".join(digests[n] for n in sorted(digests)).encode()).hexdigest()[:12]
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parse(files, version, fetched_at)
    base = root(data_dir)
    folder = base / version
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    manifest = {
        "version": version,
        "fetched_at": fetched_at,
        "files": {name: {"url": SOURCES[name], "sha256": digests[name], "bytes": len(files[name])}
                  for name in SOURCES},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    partial = base / "current.partial"
    partial.write_text(version, encoding="utf-8")
    partial.replace(base / "current")
    return manifest


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"예상보다 큰 파일입니다: {url}")
    return data

"""Pokémon images for the web pages, downloaded on request and kept on this computer.

The images are Pokémon Showdown's 5th-generation style pixel sprites (96 × 96, transparent, about
1 KB each). They cover regional and other forms (`typhlosion-hisui`, `thundurus-therian`), megas and
primals (`charizard-megax`, `groudon-primal`) and shiny variants. They are copyrighted game art: they
are fetched only when the user presses the button, stored under `data/sprites/` (local data, never
committed or redistributed) and served to the pages from there, so opening a page never contacts
another site. A public release could not ship or hot-link them.

Files are stored by Pokédex number: `248.png`, `157-hisui.png`, `6-megax.s.png`. The Showdown name is
made from the game master's English species name and the form. A form the source does not have is
remembered (`.none`) and the plain image is served instead.
"""
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

SOURCE_NAME = "Pokémon Showdown sprites (gen5)"
SOURCE_HOME = "https://play.pokemonshowdown.com/sprites/"
BASE_URL = "https://play.pokemonshowdown.com/sprites/gen5/"
SHINY_URL = "https://play.pokemonshowdown.com/sprites/gen5-shiny/"
USER_AGENT = "boxscan-sprites/0.2"
MAX_IMAGE_BYTES = 256 * 1024
NAME = re.compile(r"(\d{1,4})(?:-([a-z0-9]{1,20}))?(\.s)?\.png")
PNG = b"\x89PNG\r\n\x1a\n"

TEMP_SUFFIX = {"MEGA": "mega", "MEGA_X": "megax", "MEGA_Y": "megay", "PRIMAL": "primal"}
FORM_SUFFIX = {"alola": "alola", "alolan": "alola", "galarian": "galar", "galar": "galar", "hisuian": "hisui",
               "hisui": "hisui", "paldea": "paldea", "paldean": "paldea"}
# Forms that are the species' plain sprite in the source.
DEFAULT_FORMS = {"normal", "incarnate", "altered", "land", "ordinary", "aria", "standard", "average", "midday",
                 "baile", "shield", "solo", "red_striped", "plant", "west", "overcast", "disguised", "amped", "full_belly",
                 "hero", "zero", "family_of_four", "curly", "two_segment", "green_plumage", "chest", "single_strike"}
# Game master species names whose Showdown id is spelled differently.
SPECIES_IDS = {"nidoranfemale": "nidoranf", "nidoranmale": "nidoranm"}


def root(data_dir: Path) -> Path:
    return data_dir / "sprites"


def suffix(form: str | None = None, temp: str | None = None) -> str | None:
    """The form part of the name: mega, megax, primal, alola, galar, hisui, therian; None for the plain form."""
    if temp:
        return TEMP_SUFFIX.get(temp)
    key = (form or "").lower()
    if not key or key in DEFAULT_FORMS:
        return None
    return FORM_SUFFIX.get(key) or (re.sub(r"[^a-z0-9]", "", key)[:20] or None)


def file_name(dex: int, form_suffix: str | None = None, shiny: bool = False) -> str:
    return f"{dex}" + (f"-{form_suffix}" if form_suffix else "") + (".s" if shiny else "") + ".png"


def find(data_dir: Path, dex: int, form_suffix: str | None = None, shiny: bool = False) -> Path | None:
    """The best stored image: the form and shiny asked for, then without shiny, then the plain one."""
    folder = root(data_dir)
    for name in dict.fromkeys([file_name(dex, form_suffix, shiny), file_name(dex, form_suffix),
                               file_name(dex, None, shiny), file_name(dex)]):
        path = folder / name
        if path.is_file():
            return path
    return None


def showdown_id(reference, dex: int) -> str | None:
    forms = (reference.stats or {}).get(dex) if reference is not None else None
    if not forms:
        return None
    key = re.sub(r"[^a-z0-9]", "", forms[0].name.lower())
    return SPECIES_IDS.get(key, key) or None


def url(reference, name: str) -> str | None:
    match = NAME.fullmatch(name)
    species = showdown_id(reference, int(match[1])) if match else None
    if species is None:
        return None
    return (SHINY_URL if match[3] else BASE_URL) + species + (f"-{match[2]}" if match[2] else "") + ".png"


def wanted(entries: list[dict], reference, bosses: list[dict] | None = None) -> list[str]:
    """The image files the pages can show for this box and these bosses."""
    names = []
    for entry in entries:
        dex = entry.get("dex")
        if not dex:
            continue
        form = suffix(entry.get("form"))
        names.append(file_name(dex, form))
        if form:
            names.append(file_name(dex))  # the fallback when the source lacks the form
        if entry.get("shiny") is True:
            names.append(file_name(dex, form, True))
        if reference is not None and entry.get("shadow") is not True:
            for kind in (reference.temp_stats or {}).get(dex, {}):
                names.append(file_name(dex, suffix(None, kind)))
    for boss in bosses or []:
        dex = boss.get("dex") or boss.get("pokemon_id")
        if dex:
            names.append(file_name(dex, suffix(boss.get("form"), boss.get("temp"))))
            names.append(file_name(dex))
    return list(dict.fromkeys(names))


def missing(data_dir: Path, names: list[str]) -> list[str]:
    folder = root(data_dir)
    return [name for name in names if not (folder / name).is_file() and not (folder / f"{name}.none").is_file()]


def _fetch(address: str) -> bytes | None:
    """The image, or None when the source has no such file."""
    request = urllib.request.Request(address, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if len(data) > MAX_IMAGE_BYTES or not data.startswith(PNG):
        raise ValueError("이미지 형식이 아닙니다.")
    return data


def download(data_dir: Path, names: list[str], reference, fetch=None, workers: int = 6) -> dict:
    """Fetch the missing images. A file the source does not have is remembered (`.none`) so it is not
    asked for again; delete `data/sprites/` to start over."""
    fetch = fetch or _fetch
    folder = root(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    todo = [(name, url(reference, name)) for name in missing(data_dir, names) if NAME.fullmatch(name)]
    result = {"asked": len(todo), "saved": 0, "not_found": 0, "failed": 0, "errors": []}

    def one(item):
        name, address = item
        if address is None:  # no English species name to build the source's file name from
            return name, None, None
        try:
            return name, fetch(address), None
        except (OSError, ValueError) as error:
            return name, None, str(error)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for name, data, error in pool.map(one, todo):
            if error:
                result["failed"] += 1
                if len(result["errors"]) < 5:
                    result["errors"].append(f"{name}: {error}")
            elif data is None:
                (folder / f"{name}.none").write_text("", encoding="utf-8")
                result["not_found"] += 1
            else:
                partial = folder / f"{name}.partial"
                partial.write_bytes(data)
                partial.replace(folder / name)
                result["saved"] += 1
    (folder / "manifest.json").write_text(json.dumps({
        "source": SOURCE_NAME, "source_home": SOURCE_HOME, "base_url": BASE_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": len([p for p in folder.glob("*.png") if NAME.fullmatch(p.name)])}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return result


def status(data_dir: Path, names: list[str]) -> dict:
    folder = root(data_dir)
    stored = [p for p in folder.glob("*.png") if NAME.fullmatch(p.name)] if folder.is_dir() else []
    return {"stored": len(stored), "bytes": sum(p.stat().st_size for p in stored), "missing": len(missing(data_dir, names)),
            "wanted": len(names), "source": SOURCE_NAME, "source_home": SOURCE_HOME}

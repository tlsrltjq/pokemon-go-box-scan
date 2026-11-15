"""Validate the values a user approves and derive level and move details from them.

The browser sends what the user saw or corrected. Level and move markers are always recomputed
here from those values, so an edited CP or IV cannot leave a stale level behind.
"""
from . import level
from . import moves as move_dictionary
from .reference import Reference

STATS = ("attack", "defense", "stamina")


def _integer(value, name: str, low: int, high: int) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{name} 값이 올바르지 않습니다.")
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{name} 값이 올바르지 않습니다.") from None
    if not low <= number <= high:
        raise ValueError(f"{name}는 {low}~{high} 사이여야 합니다.")
    return number


def _text(value, name: str, limit: int = 30) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} 값이 올바르지 않습니다.")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{name}이 너무 깁니다.")
    return value or None


def normalize(raw: dict, reference: Reference | None) -> dict:
    """Return box values from user input. Raises ValueError with a message for the user."""
    if not isinstance(raw, dict):
        raise ValueError("저장할 값이 없습니다.")
    species = _text(raw.get("species"), "종 이름")
    cp = _integer(raw.get("cp"), "CP", 10, 10000)
    max_hp = _integer(raw.get("max_hp"), "최대 HP", 1, 1000)
    iv_raw = raw.get("iv") or {}
    if not isinstance(iv_raw, dict):
        raise ValueError("IV 값이 올바르지 않습니다.")
    iv_values = {stat: _integer(iv_raw.get(stat), f"IV({stat})", 0, 15) for stat in STATS}
    if any(v is None for v in iv_values.values()) and any(v is not None for v in iv_values.values()):
        raise ValueError("IV는 공격·방어·HP 세 값을 모두 입력하거나 모두 비워 두세요.")
    iv = iv_values if all(v is not None for v in iv_values.values()) else None
    fast = _text(raw.get("fast_move"), "노말 기술")
    charged_raw = raw.get("charged_moves") or []
    if not isinstance(charged_raw, list) or len(charged_raw) > 2:
        raise ValueError("스페셜 기술은 최대 2개입니다.")
    charged = [name for value in charged_raw if (name := _text(value, "스페셜 기술"))]
    if not any([species, cp, max_hp, iv, fast, charged]):
        raise ValueError("저장할 값이 없습니다.")

    estimated = level.estimate(reference, species, iv, cp, max_hp)
    levels = estimated.get("levels", [])
    voted = {
        "fast": {"value": fast, "votes": 1, "observations": 1, "power": None} if fast else None,
        "charged": [{"value": name, "votes": 1, "observations": 1, "power": None} for name in charged],
    }
    move_dictionary.annotate(voted, reference, species, estimated.get("form"))

    def move(item):
        return {key: item.get(key) for key in ("name", "move_id", "marker", "reason")} | {
            "name": item.get("name") or item["value"]}

    pokemon_id = reference.korean_names.get(species) if reference and species else None
    return {
        "species": species,
        "dex": pokemon_id,
        "form": estimated.get("form"),
        "cp": cp,
        "max_hp": max_hp,
        "iv": iv,
        "level": {
            "value": levels[0] if estimated["status"] == level.RESOLVED else None,
            "status": estimated["status"],
            "candidates": levels,
            "reference": estimated.get("reference"),
        },
        "moves": {
            "fast": move(voted["fast"]) if voted["fast"] else None,
            "charged": [move(item) for item in voted["charged"]],
        },
    }


def warnings(existing: dict, new: dict) -> list[str]:
    """Differences that suggest the candidate may be a different individual."""
    found = []
    if new.get("species") and existing.get("species") and new["species"] != existing["species"]:
        found.append(f"종이 다릅니다: 기존 {existing['species']}, 새 값 {new['species']}")
    if new.get("iv") and existing.get("iv") and new["iv"] != existing["iv"]:
        before = "/".join(str(existing["iv"][s]) for s in STATS)
        after = "/".join(str(new["iv"][s]) for s in STATS)
        found.append(f"IV가 다릅니다: 기존 {before}, 새 값 {after}. 강화로는 IV가 바뀌지 않습니다.")
    if new.get("cp") and existing.get("cp") and new["cp"] < existing["cp"]:
        found.append(f"CP가 낮아졌습니다: 기존 {existing['cp']}, 새 값 {new['cp']}")
    return found

"""Validate the values a user approves and derive level and move details from them.

The browser sends what the user saw or corrected. Level and move markers are always recomputed
here from those values, so an edited CP or IV cannot leave a stale level behind.
"""
from . import level
from . import moves as move_dictionary
from . import species as species_names
from .forms import display_name
from .reference import Reference

STATS = ("attack", "defense", "stamina")
MAX_STATES = {"dynamax", "gigantamax"}


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


def _flag(value, name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{name} 값이 올바르지 않습니다.")
    return value


def _max_moves(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 3:
        raise ValueError("맥스기술 값이 올바르지 않습니다.")
    moves = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("맥스기술 값이 올바르지 않습니다.")
        name = _text(item.get("name"), "맥스기술", 20)
        if name:
            moves.append({"name": name, "level": _integer(item.get("level"), "맥스기술 레벨", 1, 3),
                          "locked": _flag(item.get("locked"), "맥스기술 잠김") or False})
    return moves


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
    fast = _text(raw.get("fast_move"), "노말어택")
    charged_raw = raw.get("charged_moves") or []
    if not isinstance(charged_raw, list) or len(charged_raw) > 2:
        raise ValueError("스페셜어택은 최대 2개입니다.")
    charged = [name for value in charged_raw if (name := _text(value, "스페셜어택"))]
    # Lucky (반짝반짝 포켓몬) is prefilled from OCR; shiny (이로치) and special background (배경)
    # are checked by the user because they are not read from video yet.
    shiny = _flag(raw.get("shiny"), "이로치")
    lucky = _flag(raw.get("lucky"), "반짝반짝 포켓몬")
    shadow = _flag(raw.get("shadow"), "그림자")
    background = _flag(raw.get("background"), "배경")
    max_state = raw.get("max_state") or None
    if max_state is not None and max_state not in MAX_STATES:
        raise ValueError("다이맥스 값이 올바르지 않습니다.")
    max_moves = _max_moves(raw.get("max_moves"))
    mega_move_name = _text(raw.get("mega_move"), "메가진화 추가 스페셜어택")
    nickname = _text(raw.get("nickname"), "게임 별명")
    mega_level = _integer(raw.get("mega_level"), "메가 레벨", 0, 4)
    chosen_form = _text(raw.get("form"), "모습")
    mega_evolved = _flag(raw.get("mega_evolved"), "메가진화 경험")
    mega_move_off = _flag(raw.get("mega_move_off"), "메가진화 추가 스페셜어택 끄기")
    temp = raw.get("temp_evolution") or None
    if temp is not None and temp not in species_names.TEMP_NAMES:
        raise ValueError("메가진화 값이 올바르지 않습니다.")
    if not any([species, cp, max_hp, iv, fast, charged]):
        raise ValueError("저장할 값이 없습니다.")

    resolved = species_names.resolve(species, reference)  # "메가독침붕" -> 독침붕 + MEGA
    species = resolved["species"]
    temp = temp or resolved["temp_evolution"]
    if chosen_form and reference is not None and species and reference.forms(species):
        pokemon_id = reference.forms(species)[0].pokemon_id
        known = {f.form for f in reference.forms(species)} | {
            c["target"] for c in (reference.form_changes or {}).get(pokemon_id, [])}
        if chosen_form not in known:
            raise ValueError("그 종에 없는 모습입니다.")
    # The user's choice of form (히스이 블레이범, 볼트로스 영물폼) wins over the form the stats suggest.
    estimated = level.estimate(reference, species, iv, cp, max_hp, temp, chosen_form or resolved["form"])
    levels = estimated.get("levels", [])
    voted = {
        "fast": {"value": fast, "votes": 1, "observations": 1, "power": None} if fast else None,
        "charged": [{"value": name, "votes": 1, "observations": 1, "power": None} for name in charged],
    }
    if mega_move_name:
        voted["mega_plus"] = {"value": mega_move_name, "votes": 1, "observations": 1, "power": None}
    move_dictionary.annotate(voted, reference, species, estimated.get("form"))

    def move(item):
        return {key: item.get(key) for key in ("name", "move_id", "marker", "reason")} | {
            "name": item.get("name") or item["value"]}

    pokemon_id = reference.korean_names.get(species) if reference and species else None
    # While mega evolved the screen shows the mega CP/HP. Keep them as a separate observation and
    # store the regular-form values only when the level fixes them (spec 6.4: never overwrite).
    temp_observation, cp_source = None, "screen"
    if temp:
        temp_observation = {"temp_evolution": temp, "temp_name": species_names.TEMP_NAMES[temp],
                            "cp": cp, "max_hp": max_hp}
        regular = estimated.get("regular") or []
        if estimated["status"] == level.RESOLVED and len(regular) == 1:
            cp, max_hp, cp_source = regular[0]["cp"], regular[0]["max_hp"], "calculated"
        else:
            cp, max_hp, cp_source = None, None, None
    form = chosen_form or resolved["form"] or estimated.get("form")
    # Once mega evolved, the app assumes it can mega evolve again at any time. A scan while mega
    # evolved or a mega level proves it; otherwise only the user's choice sets it.
    if mega_evolved is None and (temp or (mega_level or 0) >= 1):
        mega_evolved = True
    return {
        "species": species,
        "dex": pokemon_id,
        "form": form,
        "form_name": display_name(pokemon_id, form),
        "nickname": nickname,
        "mega_level": mega_level,
        "mega_evolved": mega_evolved,
        "mega_move_off": mega_move_off,
        "cp_source": cp_source,
        "temp_observation": temp_observation,
        "max_state": max_state,
        "max_moves": max_moves,
        "fusion": species_names.fusion_options(reference, species) or None,
        "shiny": shiny,
        "lucky": lucky,
        "shadow": shadow,
        "background": background,
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
        # Additional charged attack while mega evolved; not one of the regular slots.
        "mega_move": ({key: voted["mega_plus"].get(key) for key in ("name", "move_id", "reason")}
                      if voted.get("mega_plus") else None),
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

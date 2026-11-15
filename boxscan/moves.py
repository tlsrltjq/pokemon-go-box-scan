"""Match OCR move names to the species' learnset and mark special moves with `*`.

A move gets `*` when the species can only have it through an Elite TM or an event (game master
"elite" lists), when it is the shadow/purified-only move, or when it is a known move that is not
in the species' current learnset at all. The game data does not say whether an elite move came
from a Community Day, a raid event or a legacy period, so all of them share one marker.
"""
import difflib

from .reference import Reference

MIN_SIMILARITY = 0.7
REASONS = {
    "elite": "레거시·이벤트 기술 (지금은 엘리트 기술머신으로만 배울 수 있음)",
    "shadow": "그림자·정화 포켓몬 전용 기술",
    "not_in_learnset": "이 포켓몬의 현재 기술 목록에 없는 기술 (레거시일 수 있음)",
}


def annotate(moves: dict, reference: Reference | None, species: str | None,
             form: str | None = None) -> dict:
    """Add the matched move, `*` marker and reason to each voted move in place."""
    items = ([moves["fast"]] if moves.get("fast") else []) + list(moves.get("charged", []))
    if reference is None or not reference.move_names:
        moves["dictionary"] = "unavailable"
        return moves
    learnset = _learnset(reference, species, form)
    moves["dictionary"] = "species" if learnset else "all"
    for item in items:
        item.update(_match(item, reference, learnset))
    return moves


def _learnset(reference: Reference, species: str | None, form: str | None) -> dict[str, list[str]]:
    pokemon_id = reference.korean_names.get(species.strip()) if species else None
    if pokemon_id is None or not reference.learnsets:
        return {}
    return (reference.learnsets.get(f"{pokemon_id}|{form or 'Normal'}")
            or reference.learnsets.get(f"{pokemon_id}|Normal") or {})


def _match(item: dict, reference: Reference, learnset: dict[str, list[str]]) -> dict:
    text = item["value"]
    by_korean: dict[str, list[str]] = {}
    for move_id, korean in reference.move_names.items():
        by_korean.setdefault(korean, []).append(move_id)
    candidates = {move for moves in learnset.values() for move in moves}
    pool = {reference.move_names[m]: m for m in candidates if m in reference.move_names}

    # An exact name always wins over a correction, even outside the learnset: OCR that reads a
    # real move name letter for letter is more likely right than a similar-looking guess.
    move_id, how = None, "none"
    if text in pool:
        move_id, how = pool[text], "exact"
    elif text in by_korean:
        # Known move outside this learnset (or no species): prefer the variant whose power matches.
        options = by_korean[text]
        move_id = next((m for m in options if reference.move_power.get(m) == item.get("power")),
                       options[0])
        how = "exact"
    elif pool and (close := difflib.get_close_matches(text, list(pool), n=1, cutoff=MIN_SIMILARITY)):
        move_id, how = pool[close[0]], "corrected"
    if move_id is None:
        return {"move_id": None, "match": "none", "marker": "", "reason": "기술 사전에서 찾지 못했습니다."}

    kind = None
    if learnset:
        if move_id in learnset.get("shadow", []):
            kind = "shadow"
        elif move_id in learnset.get("elite_fast", []) + learnset.get("elite_charged", []) and \
                move_id not in learnset.get("fast", []) + learnset.get("charged", []):
            kind = "elite"
        elif move_id not in candidates:
            kind = "not_in_learnset"
    result = {
        "move_id": move_id,
        "name": reference.move_names[move_id],
        "match": how,
        "marker": "*" if kind else "",
        "reason": REASONS.get(kind),
        "reference_power": reference.move_power.get(move_id),
    }
    if how == "corrected":
        result["ocr_text"] = text
    return result

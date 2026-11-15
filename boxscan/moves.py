"""Match OCR move names to the species' learnset and mark special moves with `*`.

A move gets `*` when the species can only learn it with a 대단한 기술머신 (Elite TM, the game
master's "elite" lists), when it is the shadow-only or purified-only move, or when it is a known
move that is not in the species' current learnset at all. The game data does not say whether a
대단한 기술머신-only move came from a Community Day, an event or an older learnset, so all of
them share one marker. User-facing words follow the Korean Pokémon GO client.
"""
import difflib

from .reference import Reference

MIN_SIMILARITY = 0.7
REASONS = {
    "elite_fast": "대단한 기술머신 노말어택으로만 배울 수 있는 기술 (커뮤니티 데이·이벤트·예전 기술)",
    "elite_charged": "대단한 기술머신 스페셜어택으로만 배울 수 있는 기술 (커뮤니티 데이·이벤트·예전 기술)",
    "shadow": "그림자 포켓몬 전용 기술",
    "purified": "정화된 포켓몬 전용 기술",
    "not_in_learnset": "이 포켓몬이 지금 배울 수 있는 기술 목록에 없는 기술 (예전에만 배울 수 있던 기술일 수 있음)",
    "form_change": "모습을 바꿔야 쓸 수 있는 기술 (원래 모습으로 돌아가면 다른 기술로 바뀜)",
}


def _with_particle(name: str) -> str:
    """제크로무와 / 솔가레오와 / …과: pick 와 or 과 by the final consonant."""
    last = name[-1]
    if last.isdigit():  # read as 영 일 이 삼 사 오 육 칠 팔 구
        has_final = last in "013678"
    else:
        has_final = "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 != 0
    return f"{name}{'과' if has_final else '와'}"


def _form_change_reason(reference: Reference, pokemon_id: int | None, move_id: str) -> str | None:
    for change in (reference.form_changes or {}).get(pokemon_id, []):
        if move_id in change.get("replacement_moves", []):
            if change["type"] == "FUSE" and change.get("component"):
                return f"{_with_particle(change['component'])} 합체해야 쓸 수 있는 기술 (분리하면 다른 기술로 바뀜)"
            return REASONS["form_change"]
    return None


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
        item.update(_match(item, reference, learnset, species))
    if moves.get("mega_plus"):
        moves["mega_plus"].update(_mega_plus(moves["mega_plus"], reference, species))
    return moves


MEGA_PLUS_REASON = "메가진화 중에만 쓰는 추가 스페셜어택 (메가 레벨에 따라 위력이 오름)"


def _mega_plus(item: dict, reference: Reference, species: str | None) -> dict:
    """Match a "+" move to the species' mega move data; keep the screen name when data is missing."""
    pokemon_id = reference.korean_names.get(species.strip()) if species else None
    options = (reference.mega_moves or {}).get(pokemon_id, {})
    text = item["value"].rstrip("+")
    for kind, data in options.items():
        if data.get("name") and data["name"].rstrip("+") == text:
            return {"name": data["name"], "move_id": data["move_id"], "temp_evolution": kind,
                    "reference_power": data["power"], "match": "exact", "reason": MEGA_PLUS_REASON}
    return {"name": f"{text}+", "move_id": None, "match": "none",
            "reason": MEGA_PLUS_REASON + " · 기준 데이터(게임 마스터)에 아직 없는 기술"}


def _learnset(reference: Reference, species: str | None, form: str | None) -> dict[str, list[str]]:
    pokemon_id = reference.korean_names.get(species.strip()) if species else None
    if pokemon_id is None or not reference.learnsets:
        return {}
    return (reference.learnsets.get(f"{pokemon_id}|{form or 'Normal'}")
            or reference.learnsets.get(f"{pokemon_id}|Normal") or {})


def _match(item: dict, reference: Reference, learnset: dict[str, list[str]], species: str | None) -> dict:
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
    pokemon_id = reference.korean_names.get(species.strip()) if species else None
    change_reason = _form_change_reason(reference, pokemon_id, move_id) if pokemon_id else None
    if change_reason:
        kind = "form_change"
    elif learnset:
        regular = learnset.get("fast", []) + learnset.get("charged", [])
        if move_id in learnset.get("shadow", []):
            kind = "shadow"
        elif move_id in learnset.get("purified", []):
            kind = "purified"
        elif move_id in learnset.get("elite_fast", []) and move_id not in regular:
            kind = "elite_fast"
        elif move_id in learnset.get("elite_charged", []) and move_id not in regular:
            kind = "elite_charged"
        elif move_id not in candidates:
            kind = "not_in_learnset"
    result = {
        "move_id": move_id,
        "name": reference.move_names[move_id],
        "match": how,
        "marker": "*" if kind else "",
        "marker_kind": kind,
        "reason": change_reason or REASONS.get(kind),
        "reference_power": reference.move_power.get(move_id),
    }
    if how == "corrected":
        result["ocr_text"] = text
    return result

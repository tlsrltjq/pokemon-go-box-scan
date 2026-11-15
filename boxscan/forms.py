"""Korean display names for game master form codes, following the Korean Pokémon GO client.

Only forms whose Korean names are well established are mapped. Other codes are shown as they
appear in the game data so nothing is renamed by guess.
"""

REGIONAL = {
    "Alola": "알로라의 모습",
    "Galarian": "가라르의 모습",
    "Hisuian": "히스이의 모습",
    "Paldea": "팔데아의 모습",
}
SPECIAL = {
    (150, "A"): "아머드",  # 아머드 뮤츠
    (646, "Black"): "블랙큐레무",  # names from the Korean client's fusion text
    (646, "White"): "화이트큐레무",
}


def display_name(pokemon_id: int | None, form: str | None) -> str | None:
    """None for the regular form, a Korean name when known, otherwise the game data code."""
    if not form or form == "Normal":
        return None
    if (pokemon_id, form) in SPECIAL:
        return SPECIAL[(pokemon_id, form)]
    for prefix, name in REGIONAL.items():
        if form == prefix or form.startswith(prefix + "_"):
            rest = form[len(prefix):].strip("_")
            return f"{name} ({rest})" if rest else name
    return form

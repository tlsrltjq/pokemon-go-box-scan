"""Resolve the species name shown on screen into the base species and its current state.

The appraisal caption uses the current name: "메가독침붕" while mega evolved, "원시그란돈" in primal
reversion, "블랙큐레무" when fused. Base stats and learnsets are stored under the base species,
so the state is split off here. Only names confirmed from the Korean client are mapped; other
names stay unresolved instead of being guessed.
"""
from .reference import Reference

# Shown in the Korean client's fusion text: "레시라무와 합체하여 화이트큐레무 또는 제크로무와 합체하여
# 블랙큐레무가 될 수 있습니다."
FUSED_NAMES = {"블랙큐레무": ("큐레무", "Black"), "화이트큐레무": ("큐레무", "White")}
TEMP_NAMES = {"MEGA": "메가진화", "MEGA_X": "메가진화 X", "MEGA_Y": "메가진화 Y", "PRIMAL": "원시회귀"}


def resolve(name: str | None, reference: Reference | None) -> dict:
    """Return {"species", "screen_name", "temp_evolution", "form"} for a name read on screen."""
    result = {"species": name, "screen_name": name, "temp_evolution": None, "form": None}
    if not name or reference is None:
        return result
    names = reference.korean_names
    if name in names:
        return result
    if name in FUSED_NAMES:
        species, form = FUSED_NAMES[name]
        return {**result, "species": species, "form": form}
    for prefix, kind in (("메가", "MEGA"), ("원시", "PRIMAL")):
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        if kind == "MEGA" and rest[-1:] in ("X", "Y") and rest[:-1] in names:
            return {**result, "species": rest[:-1], "temp_evolution": f"MEGA_{rest[-1]}"}
        if rest in names:
            return {**result, "species": rest, "temp_evolution": kind}
    return result


def single_mega(reference: Reference | None, species: str | None) -> str | None:
    """The temp evolution when a species has exactly one (used when only the timer was seen)."""
    if reference is None or not species or not reference.temp_stats:
        return None
    options = reference.temp_stats.get(reference.korean_names.get(species), {})
    return next(iter(options)) if len(options) == 1 else None


def fusion_options(reference: Reference | None, species: str | None) -> list[dict]:
    """Fusions and form changes the regular form can make, for display."""
    if reference is None or not species or not reference.form_changes:
        return []
    pokemon_id = reference.korean_names.get(species)
    fused = {form: name for name, (base, form) in FUSED_NAMES.items() if base == species}
    return [
        {"type": change["type"], "component": change["component"], "target": change["target"],
         "target_name": fused.get(change["target"])}
        for change in reference.form_changes.get(pokemon_id, [])
    ]

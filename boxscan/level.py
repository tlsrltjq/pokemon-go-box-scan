"""Level candidates from species, IV, CP and max HP (spec v1.2 section 5.3).

Every level in the CPM table is tried. A level stays a candidate when the CP formula gives the
observed CP and, if max HP was read, the HP formula gives the observed max HP. Power-up costs,
best buddy boosts and mega forms are not used yet.
"""
import math

from .forms import display_name
from .reference import BaseStats, Reference

MAX_LEVEL = 50.0  # highest level a Pokémon can be powered up to; best buddy boost is separate
RESOLVED = "RESOLVED_BY_CONSTRAINTS"
AMBIGUOUS = "AMBIGUOUS"
CONFLICT = "CONFLICT"
MISSING_INPUT = "MISSING_INPUT"
NO_REFERENCE = "NO_REFERENCE"
UNKNOWN_SPECIES = "UNKNOWN_SPECIES"


def cp(base: BaseStats, iv: dict, cpm: float) -> int:
    attack = base.attack + iv["attack"]
    defense = base.defense + iv["defense"]
    stamina = base.stamina + iv["stamina"]
    return max(10, math.floor(attack * math.sqrt(defense) * math.sqrt(stamina) * cpm * cpm / 10))


def hp(base: BaseStats, iv: dict, cpm: float) -> int:
    return max(10, math.floor((base.stamina + iv["stamina"]) * cpm))


def candidates(base: BaseStats, iv: dict, observed_cp: int, observed_max_hp: int | None,
               cpm_table: dict[float, float]) -> list[float]:
    return [
        level for level, cpm in sorted(cpm_table.items())
        if level <= MAX_LEVEL and cp(base, iv, cpm) == observed_cp
        and (observed_max_hp is None or hp(base, iv, cpm) == observed_max_hp)
    ]


def _stat_options(reference: Reference, species: str, temp_evolution: str | None,
                  form: str | None) -> list[BaseStats]:
    """Stats to try: mega/primal stats while temporarily evolved, a forced form, or all forms."""
    forms = reference.forms(species)
    if not forms:
        return []
    pokemon_id = forms[0].pokemon_id
    if temp_evolution:
        stats = (reference.temp_stats or {}).get(pokemon_id, {}).get(temp_evolution)
        return [stats] if stats else []
    if form and form != "Normal":
        for change in (reference.form_changes or {}).get(pokemon_id, []):
            if change["target"] == form and change.get("stats"):
                attack, defense, stamina = change["stats"]
                return [BaseStats(pokemon_id, forms[0].name, form, attack, defense, stamina)]
        return [f for f in forms if f.form == form]
    return forms


def estimate(reference: Reference | None, species: str | None, iv: dict | None,
             observed_cp: int | None, observed_max_hp: int | None,
             temp_evolution: str | None = None, form: str | None = None) -> dict:
    """Return a level result with status, candidate levels and the form used.

    While mega evolved or in primal reversion the screen shows that state's CP and HP, so the
    level is found with those stats; the regular-form CP and HP at that level are added as
    calculated values.
    """
    result: dict = {"status": MISSING_INPUT, "levels": [], "form": None}
    if reference is None:
        return {**result, "status": NO_REFERENCE}
    result["reference"] = reference.version
    result["max_level_checked"] = min(MAX_LEVEL, max(reference.cpm))
    missing = [name for name, value in (("species", species), ("iv", iv), ("cp", observed_cp))
               if value is None]
    if missing:
        return {**result, "missing": missing}
    if not reference.forms(species):
        return {**result, "status": UNKNOWN_SPECIES}
    forms = _stat_options(reference, species, temp_evolution, form)
    if temp_evolution:
        result["temp_evolution"] = temp_evolution
    if not forms:
        return {**result, "status": UNKNOWN_SPECIES}
    matches = {}
    for form in forms:
        levels = candidates(form, iv, observed_cp, observed_max_hp, reference.cpm)
        if levels:
            matches[form.form] = levels
    result["used_max_hp"] = observed_max_hp is not None
    if not matches:
        return {**result, "status": CONFLICT}
    # Prefer the regular form when it explains the reading; other forms stay visible for review.
    form = "Normal" if "Normal" in matches else sorted(matches)[0]
    levels = matches[form]
    pokemon_id = forms[0].pokemon_id
    if temp_evolution:
        regular = reference.forms(species)[0]
        result["regular"] = [
            {"level": value, "cp": cp(regular, iv, reference.cpm[value]),
             "max_hp": hp(regular, iv, reference.cpm[value])} for value in levels]
        form = "Normal"
        matches = {form: levels}
    return {
        **result,
        "status": RESOLVED if len(levels) == 1 and len(matches) == 1 else AMBIGUOUS,
        "levels": levels,
        "form": form,
        "form_name": display_name(pokemon_id, form),
        # Keyed by the Korean display name ("기본 모습" for the regular form).
        "other_forms": {display_name(pokemon_id, name) or "기본 모습": found
                        for name, found in matches.items() if name != form},
    }

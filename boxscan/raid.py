"""Normal raid recommendation (spec v1.2 sections 7 and 8.1).

The goal is the strongest party for a solo raid: one that defeats the boss beats one that does not,
among those that defeat it the faster clear wins, and among those that do not the most damage before
the time limit ends or (without re-entry) everyone faints wins. The engine is a deterministic event
simulation:

- Damage: floor(0.5 * power * attack / defense * STAB * weather * type) + 1, values from the game
  master. Attack and defense are (base + IV) * CPM for my Pokémon and (base + 15) * tier CPM for
  the boss; boss HP comes from the tier table below.
- My Pokémon start at full HP with 0 energy, use the fast attack until the chosen charged attack is
  affordable, then use it. Damage lands at the move's damage window. Energy comes from my fast
  attacks and from damage taken (0.5 per HP). A fainted Pokémon's pending hit is lost. The next one
  enters after the swap time.
- Dodging (on by default, the way people play solo): every boss fast attack hits; every boss charged
  attack is dodged, so it deals the game's dodged share (1 - dodgeDamageReductionPercent) and my next
  action waits dodgeDurationMs. Without dodging every attack hits in full.
- The boss uses a move, then waits enemyAttackInterval + 0.5 s (the midpoint of the 1.5–2.5 s
  spread). It uses its charged attack on every second chance once it has the energy (about 50 %).
  It gains energy from its moves and from damage taken.
- Boss movesets that are not chosen are all simulated and reported as an even average with the
  lowest and highest result. That is a scenario average, not the game's real probabilities.

Party search: every eligible Pokémon picks the charged attack that does better in a solo run (by the
same ranking); the strongest candidates (by damage per second) are kept, every party of up to six
from them is estimated from the solo runs (damage up to the boss's HP and the time to get there), and
the best few are simulated in full. The search is exhaustive over
the kept candidates, not over every possible action, and the result says so.

Mega evolution (optional): box Pokémon that can mega evolve or reverse into a primal form are also
tried in that form (its stats and types, and the "+" move the game master gives that mega). Mode
"evolved" uses only individuals that have mega evolved before (the app assumes they can do it again
at any time; no energy or cooldown is tracked), mode "all" every one that can. A party holds
at most one of them, like the game's one active mega. While it is on the field its own attacks get
the game master's mega boost (same type x1.3, other types x1.1; primal Groudon, Kyogre and mega
Rayquaza boost their listed types). Alone in a raid my other Pokémon wait on the bench, so they do
not get the boost.
"""
import heapq
import itertools
import math
from dataclasses import dataclass, field

from . import level as level_rules
from .forms import display_name
from .reference import Reference
from .species import TEMP_NAMES, mega_evolved

ENGINE_VERSION = "raid-sim/4"
PARTY_SIZE = 6
CANDIDATE_LIMIT = 12  # best by each measure (damage per second, total damage)
SHORTLIST_LIMIT = 18  # at most this many are combined (18 choose 6 = 18,564 parties)
MEGA_CANDIDATE_LIMIT = 4
FULL_SIM_PARTIES = 8
BOSS_EXTRA_DELAY_MS = 500
# Game master battleSettings values, used when the saved reference predates reading them.
DODGE_REDUCTION_DEFAULT = 0.75
DODGE_MS_DEFAULT = 500

# Raid tiers. HP and CPM as listed on Bulbapedia's "Raid Battle (GO)" page (checked 2026-11-16);
# the 22,500 HP tier as listed by GamePress. Time limits are the app's scenario defaults, not
# confirmed game data, and can be changed per request.
RULESET = {
    "version": "raid-ruleset/2026-11-16",
    "tiers": {
        "1": {"name": "1성", "hp": 600, "cpm": 0.5974, "time_limit": 180},
        "3": {"name": "3성", "hp": 3600, "cpm": 0.73, "time_limit": 180},
        "mega": {"name": "메가", "hp": 9000, "cpm": 0.79, "time_limit": 300},
        "5": {"name": "5성 (전설)", "hp": 15000, "cpm": 0.79, "time_limit": 300},
        "mega_legendary": {"name": "메가 전설·원시회귀", "hp": 22500, "cpm": 0.79, "time_limit": 300},
    },
    "sources": [
        "https://bulbapedia.bulbagarden.net/wiki/Raid_Battle_(GO)",
        "https://gamepress.gg/pokemongo/q-a/raid-boss-cp-multiplier",
    ],
}
WEATHER_NAMES = {"": "날씨 보너스 없음", "CLEAR": "맑음", "RAINY": "비", "PARTLY_CLOUDY": "구름 조금",
                 "OVERCAST": "흐림", "WINDY": "바람", "SNOW": "눈", "FOG": "안개"}
TYPE_NAMES = {"NORMAL": "노말", "FIGHTING": "격투", "FLYING": "비행", "POISON": "독", "GROUND": "땅",
              "ROCK": "바위", "BUG": "벌레", "GHOST": "고스트", "STEEL": "강철", "FIRE": "불꽃", "WATER": "물",
              "GRASS": "풀", "ELECTRIC": "전기", "PSYCHIC": "에스퍼", "ICE": "얼음", "DRAGON": "드래곤",
              "DARK": "악", "FAIRY": "페어리"}


class RaidError(ValueError):
    """The request cannot be calculated. The message is shown to the user."""


@dataclass
class Move:
    move_id: str
    name: str
    type: str
    power: float
    energy: int
    duration_ms: int
    damage_ms: int
    bonus: float = 1.0  # mega "+" moves get a multiplier by mega level


@dataclass
class Fighter:
    key: str
    name: str
    attack: float
    defense: float
    hp: int
    types: list[str]
    fast: Move
    charged: list[Move]
    info: dict = field(default_factory=dict)


def _move(reference: Reference, move_id: str | None) -> Move | None:
    stats = (reference.move_stats or {}).get(move_id or "")
    if not stats or not stats.get("duration_ms"):
        return None
    return Move(move_id, reference.move_names.get(move_id, move_id), stats["type"], stats["power"],
                stats["energy"], stats["duration_ms"], stats["damage_start_ms"])


def _types(reference: Reference, pokemon_id: int, form: str | None, temp: str | None = None) -> list[str]:
    types = reference.types or {}
    return (types.get(f"{pokemon_id}|{temp}") if temp else None) or types.get(f"{pokemon_id}|{form or 'Normal'}") \
        or types.get(f"{pokemon_id}|Normal") or []


def boss(reference: Reference, species: str, tier: str, form: str | None = None,
         temp: str | None = None) -> dict:
    """Boss stats and its possible movesets for a tier."""
    if tier not in RULESET["tiers"]:
        raise RaidError("레이드 등급을 다시 선택하세요.")
    options = level_rules._stat_options(reference, species, temp, form)
    if not options:
        raise RaidError("기준 데이터에서 보스를 찾지 못했습니다. 종·모습·메가진화를 확인하세요.")
    stats = options[0]
    rule = RULESET["tiers"][tier]
    pokemon_id = stats.pokemon_id
    learnset = (reference.learnsets or {}).get(f"{pokemon_id}|{form or 'Normal'}") \
        or (reference.learnsets or {}).get(f"{pokemon_id}|Normal") or {}
    fast = [m for m in (_move(reference, i) for i in learnset.get("fast", [])) if m]
    charged = [m for m in (_move(reference, i) for i in learnset.get("charged", [])) if m]
    if not fast or not charged:
        raise RaidError("보스의 기술 데이터를 찾지 못했습니다.")
    return {
        "species": species, "form": form, "form_name": display_name(pokemon_id, form), "temp": temp,
        "tier": tier, "tier_name": rule["name"], "hp": rule["hp"],
        "attack": (stats.attack + 15) * rule["cpm"], "defense": (stats.defense + 15) * rule["cpm"],
        "types": _types(reference, pokemon_id, form, temp), "fast": fast, "charged": charged,
    }


def temp_name(species: str, temp: str) -> str:
    """The game's name of the mega / primal form: 메가리자몽X, 원시그란돈."""
    if temp == "PRIMAL":
        return f"원시{species}"
    return f"메가{species}" + temp.removeprefix("MEGA").removeprefix("_")


def attacker(reference: Reference, entry: dict, temp: str | None = None) -> tuple[Fighter | None, str | None]:
    """A box entry as a fighter, or the reason it cannot be used. With temp, the same Pokémon mega
    evolved (or in primal reversion)."""
    species, iv, lvl = entry.get("species"), entry.get("iv"), (entry.get("level") or {}).get("value")
    if not species or not reference.forms(species):
        return None, "종을 기준 데이터에서 찾지 못함"
    if not iv:
        return None, "IV 없음"
    if lvl is None:
        return None, "레벨이 하나로 정해지지 않음"
    form = entry.get("form") or "Normal"
    options = level_rules._stat_options(reference, species, temp, None if temp else form)
    if not options:
        return None, "메가진화 종족값을 찾지 못함" if temp else "모습의 종족값을 찾지 못함"
    stats = options[0]
    moves = entry.get("moves") or {}
    fast = _move(reference, (moves.get("fast") or {}).get("move_id"))
    charged = [m for m in (_move(reference, (c or {}).get("move_id")) for c in moves.get("charged") or []) if m]
    if fast is None:
        return None, "노말어택을 기술 사전과 연결하지 못함"
    if not charged:
        return None, "스페셜어택을 기술 사전과 연결하지 못함"
    cpm = reference.cpm.get(float(lvl))
    if cpm is None:
        return None, "레벨의 CP 배율이 없음"
    shadow = entry.get("shadow") is True
    if temp and shadow:
        return None, "그림자 포켓몬은 메가진화할 수 없음"
    # The box keeps the regular form's max HP; a mega's HP comes from its own stamina.
    hp = (None if temp else entry.get("max_hp")) or max(10, math.floor((stats.stamina + iv["stamina"]) * cpm))
    # Fused or other forms show their own name (블랙큐레무), like the box does.
    # A fused form has its own name (블랙큐레무); other forms are shown after the species.
    form_name = entry.get("form_name") if form != "Normal" else None
    marks = "".join(mark for key, mark in (("shiny", "*"), ("lucky", "♣"), ("background", "◆")) if entry.get(key) is True)
    name = temp_name(species, temp) + marks if temp else (
        f"{species}{marks} ({form_name})" if form_name and form_name.endswith(("모습", "용사", "다이맥스"))
        else (form_name or species) + marks)
    attack, defense = (stats.attack + iv["attack"]) * cpm, (stats.defense + iv["defense"]) * cpm
    if shadow:  # shadow bonus from the game's battle settings: more attack, less defense
        attack *= reference.battle.get("shadowPokemonAttackBonusMultiplier", 1.2)
        defense *= reference.battle.get("shadowPokemonDefenseBonusMultiplier", 0.8333333)
        name += " (그림자)"
    types = _types(reference, stats.pokemon_id, None if temp else form, temp)
    info = {"code": entry.get("code"), "revision": entry.get("revision"), "level": lvl, "iv": iv, "shadow": shadow,
            "mega": None}
    if temp:
        boosts = reference.temp_boosts or {}
        boosted = (boosts.get("types") or {}).get(str(stats.pokemon_id)) or types
        plus, confirmed = _mega_plus(reference, entry, stats.pokemon_id, temp)
        if plus:
            charged = [*charged, plus]
        info["mega"] = {"kind": temp, "name": TEMP_NAMES.get(temp, temp), "boost_types": boosted,
                        "same_type": boosts.get("same_type", 1.0), "different_type": boosts.get("different_type", 1.0),
                        "plus_move": plus.name if plus else None, "plus_confirmed": confirmed,
                        "evolved": mega_evolved(entry), "level": entry.get("mega_level")}
    return Fighter(entry["id"], name, attack, defense, int(hp), types, fast, charged, info), None


def _mega_plus(reference: Reference, entry: dict, pokemon_id: int, temp: str) -> tuple[Move | None, bool]:
    """The mega's "+" charged attack and whether a scan confirmed it. A mega that gets one in the game
    master is assumed to have it unless the user turned it off; a "+" move read while mega evolved
    confirms it. Its multiplier follows the saved mega level (game master values); without one the
    lowest level that unlocks it is assumed."""
    data = (reference.mega_moves or {}).get(pokemon_id, {}).get(temp) or {}
    if not data.get("move_id") or entry.get("mega_move_off") is True:
        return None, False
    move = _move(reference, data["move_id"])
    if move is None:
        return None, False
    confirmed = (entry.get("mega_move") or {}).get("move_id") == data["move_id"]
    levels = (reference.temp_boosts or {}).get("special_move") or [1.0]
    level = entry.get("mega_level")
    index = level if isinstance(level, int) and 1 <= level < len(levels) else min(1, len(levels) - 1)
    move.name, move.bonus = data.get("name") or move.name, levels[index]
    return move, confirmed


def mega_bonus(fighter: Fighter, move: Move) -> float:
    """The attack multiplier while this fighter is the active mega (1.0 for others)."""
    mega = fighter.info.get("mega")
    boost = 1.0
    if mega:
        boost = mega["same_type"] if move.type in mega["boost_types"] else mega["different_type"]
    return boost * move.bonus


def damage(reference: Reference, move: Move, attack: float, defense: float, attacker_types: list[str],
           defender_types: list[str], weather_types: list[str], bonus: float = 1.0) -> int:
    stab = reference.battle["sameTypeAttackBonusMultiplier"] if move.type in attacker_types else 1.0
    boost = 1.2 if move.type in weather_types else 1.0
    effective = math.prod(reference.type_chart[move.type][t] for t in defender_types)
    return math.floor(0.5 * move.power * attack / defense * stab * boost * effective * bonus) + 1


def dodge_settings(reference: Reference) -> tuple[float, int, bool]:
    """(share of a dodged hit's damage that still lands, dodge time in ms, read from the game data)."""
    settings = reference.battle or {}
    known = "dodgeDamageReductionPercent" in settings and "dodgeDurationMs" in settings
    reduction = float(settings.get("dodgeDamageReductionPercent", DODGE_REDUCTION_DEFAULT))
    return 1.0 - reduction, int(settings.get("dodgeDurationMs", DODGE_MS_DEFAULT)), known


def simulate(reference: Reference, party: list[tuple[Fighter, int]], foe: dict, boss_fast: Move,
             boss_charged: Move, time_limit_s: float, weather: str = "", rejoin_delay_s: float | None = None,
             trainers: int = 1, dodge: bool = False) -> dict:
    """Run one battle. party = [(fighter, index of the charged attack to use)], in order.

    With trainers > 1 the others are assumed to deal as much as I do at the same moments, so the boss
    loses HP that many times faster; only my share is counted as my damage. With dodge, every boss
    charged attack is dodged: only the dodged share lands and my next action waits the dodge time."""
    weather_types = (reference.weather or {}).get(weather, []) if weather else []
    settings = reference.battle
    limit = int(time_limit_s * 1000)
    max_energy = settings["maximumEnergy"]
    swap = settings["swapDurationMs"]
    boss_delay = int(settings["enemyAttackInterval"] * 1000) + BOSS_EXTRA_DELAY_MS
    shadow = foe.get("shadow")
    dodged_share, dodge_ms, _ = dodge_settings(reference)
    dodge_wait = 0  # ms my next action is held back by a dodge

    def tables(attack_scale: float, defense_scale: float):
        defense, attack = foe["defense"] * defense_scale, foe["attack"] * attack_scale
        return ([damage(reference, f.fast, f.attack, defense, f.types, foe["types"], weather_types,
                        mega_bonus(f, f.fast)) for f, _ in party],
                [damage(reference, f.charged[c], f.attack, defense, f.types, foe["types"], weather_types,
                        mega_bonus(f, f.charged[c])) for f, c in party],
                [(damage(reference, boss_fast, attack, f.defense, foe["types"], f.types, weather_types),
                  damage(reference, boss_charged, attack, f.defense, foe["types"], f.types, weather_types))
                 for f, _ in party])

    # A shadow raid boss turns enraged (more attack and defense) once its HP falls to the threshold,
    # and calms again only in the scenario where the user uses Purified Gems.
    normal = tables(1.0, 1.0)
    angry = tables(shadow["attack"], shadow["defense"]) if shadow else normal
    enraged, enraged_ms, calmed_ms = False, None, None
    stats = [{"damage": 0, "time_ms": 0, "faints": 0, "charged_uses": 0} for _ in party]
    boss_hp, boss_energy, boss_charge_turn = foe["hp"], 0, False
    current, hp, energy, entered = 0, party[0][0].hp, 0, 0
    alive_hp = [f.hp for f, _ in party]
    rejoins, generation = 0, 0
    events: list = []
    order = itertools.count()

    def push(time, kind, data=None):
        heapq.heappush(events, (time, 0 if kind.endswith("hit") else 1, next(order), kind, data))

    push(0, "attacker_move", generation)
    push(boss_delay, "boss_move")
    end_time, defeated, active = limit, False, True
    while events:
        time, _, _, kind, data = heapq.heappop(events)
        if time >= limit or boss_hp <= 0:
            break
        if kind == "attacker_move":
            if data != generation or not active:
                continue
            if dodge_wait:
                push(time + dodge_wait, "attacker_move", generation)
                dodge_wait = 0
                continue
            fighter, choice = party[current]
            move = fighter.charged[choice]
            if energy >= -move.energy:
                energy += move.energy
                stats[current]["charged_uses"] += 1
                push(time + move.damage_ms, "attacker_hit", (generation, 1, 0))
            else:
                move = fighter.fast
                push(time + move.damage_ms, "attacker_hit", (generation, 0, move.energy))
            push(time + move.duration_ms, "attacker_move", generation)
        elif kind == "attacker_hit":
            hit_generation, which, gained = data
            if hit_generation != generation or not active:
                continue
            total = min((angry if enraged else normal)[which][current] * trainers, boss_hp)
            dealt = total / trainers
            boss_hp -= total
            stats[current]["damage"] += dealt
            energy = min(max_energy, energy + gained)
            boss_energy = min(max_energy, boss_energy + math.ceil(dealt * settings["bossEnergyRegenerationPerHealthLost"]))
            if boss_hp <= 0:
                defeated, end_time = True, time
            elif shadow and enraged_ms is None and boss_hp <= shadow["enrage_at"] * foe["hp"]:
                enraged, enraged_ms = True, time
                if shadow.get("calm_after_ms") is not None:
                    push(time + shadow["calm_after_ms"], "calm")
        elif kind == "calm":
            enraged, calmed_ms = False, time
        elif kind == "boss_move":
            use_charged = boss_energy >= -boss_charged.energy and boss_charge_turn
            if boss_energy >= -boss_charged.energy:
                boss_charge_turn = not boss_charge_turn
            move = boss_charged if use_charged else boss_fast
            if use_charged:
                boss_energy += move.energy
            push(time + move.damage_ms, "boss_hit", (use_charged, move.energy))
            push(time + move.duration_ms + boss_delay, "boss_move")
        elif kind == "boss_hit":
            charged, gained = data
            if not charged:
                boss_energy = min(max_energy, boss_energy + gained)
            if not active or time < entered:
                continue  # nobody on the field during a swap or a re-entry
            dealt = (angry if enraged else normal)[2][current][1 if charged else 0]
            if charged and dodge:
                dealt = max(1, math.floor(dealt * dodged_share))
                dodge_wait += dodge_ms
            hp -= dealt
            energy = min(max_energy, energy + math.ceil(dealt * settings["energyDeltaPerHealthLost"]))
            if hp <= 0:
                stats[current]["time_ms"] += time - entered
                stats[current]["faints"] += 1
                alive_hp[current] = 0
                generation += 1
                following = next((i for i in range(current + 1, len(party)) if alive_hp[i] > 0), None)
                if following is None and rejoin_delay_s is not None:
                    rejoins += 1
                    alive_hp = [f.hp for f, _ in party]
                    following, start = 0, time + int(rejoin_delay_s * 1000)
                else:
                    start = time + swap
                if following is None:
                    active, end_time = False, time
                    continue
                current, hp, energy, entered, dodge_wait = following, party[following][0].hp, 0, start, 0
                push(start, "attacker_move", generation)
    if active:
        stats[current]["time_ms"] += min(end_time, limit) - entered
    for s in stats:
        s["damage"] = round(s["damage"])
    total = sum(s["damage"] for s in stats)
    return {"damage": total, "defeated": defeated, "end_ms": min(end_time, limit),
            "faints": sum(s["faints"] for s in stats), "rejoins": rejoins, "members": stats,
            "enraged_ms": enraged_ms, "calmed_ms": calmed_ms}


def _scenarios(foe: dict, boss_fast: str | None, boss_charged: str | None) -> list[tuple[Move, Move]]:
    fast = [m for m in foe["fast"] if not boss_fast or m.move_id == boss_fast]
    charged = [m for m in foe["charged"] if not boss_charged or m.move_id == boss_charged]
    if not fast or not charged:
        raise RaidError("선택한 보스 기술이 보스의 기술 목록에 없습니다.")
    return [(f, c) for f in fast for c in charged]


def rank(outcomes: list[tuple[bool, float, float]]) -> tuple:
    """Solo raid ranking over boss moveset scenarios, larger is better. Each outcome is
    (boss defeated, my damage, end ms): more scenarios cleared first, then more damage where the boss
    survives, then the faster clears."""
    return (sum(1 for defeated, _, _ in outcomes if defeated),
            round(sum(dealt for defeated, dealt, _ in outcomes if not defeated)),
            -sum(end for defeated, _, end in outcomes if defeated))


def _run_rank(runs: list[dict]) -> tuple:
    return rank([(r["defeated"], r["damage"], r["end_ms"]) for r in runs])


def _estimate(members: list[tuple[int, int]], limit_ms: int, swap_ms: int, rejoin_ms: int | None,
              target: float) -> tuple[bool, float, float]:
    """(defeated, damage, end ms) of a party, estimated from solo runs (damage, alive ms) per member in
    order. target is my share of the boss's HP; damage stops there."""
    remaining, total, elapsed = limit_ms, 0.0, 0.0
    while remaining > 0:
        for dealt, alive in members:
            if remaining <= 0:
                break
            alive = max(alive, 1)
            span = min(alive, remaining)
            gained = dealt * span / alive
            if dealt > 0 and total + gained >= target:
                return True, target, elapsed + (target - total) * alive / dealt
            total += gained
            elapsed += span
            remaining -= span
            if span < alive:
                remaining = 0
                break
            elapsed += swap_ms
            remaining -= swap_ms
        if rejoin_ms is None:
            break
        elapsed += rejoin_ms - swap_ms
        remaining -= rejoin_ms - swap_ms
    return False, total, min(elapsed, limit_ms)


def _recommend(reference: Reference, entries: list[dict], species: str, tier: str, form: str | None = None,
              temp: str | None = None, weather: str = "", time_limit_s: float | None = None,
              rejoin_delay_s: float | None = None, boss_fast: str | None = None,
              boss_charged: str | None = None, mega: str = "off",
              shadow: dict | None = None, compare_shadow: bool = True, trainers: int = 1,
              dodge: bool = True) -> dict:
    if mega != "off" and not reference.temp_boosts:
        raise RaidError("기준 데이터에 메가진화 보너스가 없습니다. `python -m boxscan reference rebuild`를 실행하세요.")
    if weather not in WEATHER_NAMES:
        raise RaidError("날씨를 다시 선택하세요.")
    foe = boss(reference, species, tier, form, temp)
    if shadow is not None:
        foe["shadow"] = _shadow_settings(shadow, tier, temp)
    limit_s = time_limit_s or RULESET["tiers"][tier]["time_limit"]
    if not 30 <= limit_s <= 600:
        raise RaidError("제한 시간은 30~600초 사이로 입력하세요.")
    if not isinstance(trainers, int) or isinstance(trainers, bool) or not 1 <= trainers <= 20:
        raise RaidError("함께하는 트레이너는 1~20명으로 입력하세요.")
    if rejoin_delay_s is not None and not 0 <= rejoin_delay_s <= 120:
        raise RaidError("재입장 지연은 0~120초 사이로 입력하세요.")
    scenarios = _scenarios(foe, boss_fast, boss_charged)

    def candidate(fighter):
        options = []
        for index, _ in enumerate(fighter.charged):
            runs = [simulate(reference, [(fighter, index)], foe, f, c, limit_s, weather, trainers=trainers,
                             dodge=dodge) for f, c in scenarios]
            solo = [(r["damage"], max(r["members"][0]["time_ms"], 1)) for r in runs]
            cleared = [r["end_ms"] for r in runs if r["defeated"]]
            options.append({"rank": _run_rank(runs), "index": index, "solo": solo,
                            "damage": sum(d for d, _ in solo) / len(solo),
                            "clear_s": sum(cleared) / len(cleared) / 1000 if len(cleared) == len(runs) else None})
        options.sort(key=lambda option: option["rank"], reverse=True)
        best = options[0]
        dps = sum(d / t for d, t in best["solo"]) / len(best["solo"]) * 1000
        return {"fighter": fighter, "choice": best["index"], "solo": best["solo"], "dps": dps, "options": options}

    excluded, candidates, megas = [], [], []
    for entry in entries:
        fighter, reason = attacker(reference, entry)
        if fighter is None:
            excluded.append({"code": entry.get("code"), "name": entry.get("species") or "종 미확인", "reason": reason})
            continue
        candidates.append(candidate(fighter))
        if mega == "all" or (mega == "evolved" and mega_evolved(entry)):
            pokemon_id = reference.korean_names.get(entry["species"].strip())
            for kind in (reference.temp_stats or {}).get(pokemon_id, {}):
                form_fighter, _ = attacker(reference, entry, kind)
                if form_fighter is not None:
                    megas.append(candidate(form_fighter))
    if not candidates:
        return _report(reference, foe, limit_s, weather, rejoin_delay_s, scenarios, [], [], excluded,
                       {"candidates": 0, "considered": 0, "combinations": 0, "approximate": False,
                        "mega_candidates": 0}, mega=mega)

    candidates.sort(key=lambda c: c["dps"], reverse=True)
    megas.sort(key=lambda c: c["dps"], reverse=True)
    considered = _shortlist(candidates) + _shortlist(megas, MEGA_CANDIDATE_LIMIT, MEGA_CANDIDATE_LIMIT)
    size = min(PARTY_SIZE, len({c["fighter"].key for c in considered}))
    swap = reference.battle["swapDurationMs"]
    rejoin_ms = None if rejoin_delay_s is None else int(rejoin_delay_s * 1000)

    def ordered(team):
        return sorted(team, key=lambda c: c["dps"], reverse=True)

    def allowed(team):  # one Pokémon once, and at most one mega at a time
        return (len({c["fighter"].key for c in team}) == len(team)
                and sum(1 for c in team if c["fighter"].info.get("mega")) <= 1)

    estimates = []
    target = foe["hp"] / trainers  # my share of the boss's HP
    combos = [team for team in itertools.combinations(considered, size) if allowed(team)]
    for team in combos:
        team = ordered(team)
        score = rank([_estimate([c["solo"][s] for c in team], int(limit_s * 1000), swap, rejoin_ms, target)
                      for s in range(len(scenarios))])
        estimates.append((score, team))
    estimates.sort(key=lambda item: item[0], reverse=True)

    best = None
    for _, team in estimates[:FULL_SIM_PARTIES]:
        runs = [simulate(reference, [(c["fighter"], c["choice"]) for c in team], foe, f, ch, limit_s, weather,
                         rejoin_delay_s, trainers, dodge) for f, ch in scenarios]
        score = _run_rank(runs)
        if best is None or score > best[0]:
            best = (score, team, runs)
    _, team, runs = best
    search = {"candidates": len(candidates), "considered": len(considered), "combinations": len(combos),
              "fully_simulated": min(FULL_SIM_PARTIES, len(estimates)), "mega_candidates": len(megas),
              "approximate": len(candidates) > len(considered) - len([c for c in considered if c in megas])
              or len(megas) > MEGA_CANDIDATE_LIMIT}
    report = _report(reference, foe, limit_s, weather, rejoin_delay_s, scenarios, team, runs, excluded, search,
                     shortfall=len(candidates) < PARTY_SIZE, mega=mega, dodge=dodge)
    report["trainers"] = trainers
    if trainers > 1:
        report["assumptions"].insert(0, f"함께하는 트레이너 {trainers}명: 다른 트레이너도 나와 같은 피해를 같은 때 준다고 보고 "
                                     "보스 HP가 그만큼 빨리 줄어듭니다. 결과의 피해는 내 몫입니다.")
    if foe.get("shadow") and runs:
        settings = foe["shadow"]
        enraged = [r["enraged_ms"] for r in runs if r["enraged_ms"] is not None]
        report["shadow"] = {**settings, "enraged_runs": len(enraged), "runs": len(runs),
                            "enraged_s": round(sum(enraged) / len(enraged) / 1000, 1) if enraged else None}
        if compare_shadow:
            # The same party when the boss is calmed right away (or, if the user chose calming, never).
            other = {**settings, "calm_after_ms": 0 if settings.get("calm_after_ms") is None else None}
            other_foe = {**foe, "shadow": other}
            other_runs = [simulate(reference, [(c["fighter"], c["choice"]) for c in team], other_foe, f, ch, limit_s,
                                   weather, rejoin_delay_s, trainers, dodge) for f, ch in scenarios]
            cleared = [r["end_ms"] / 1000 for r in other_runs if r["defeated"]]
            report["shadow"]["comparison"] = {
                "calm_after_s": None if other["calm_after_ms"] is None else other["calm_after_ms"] / 1000,
                "damage": round(sum(r["damage"] for r in other_runs) / len(other_runs), 1),
                "clear_s": round(sum(cleared) / len(cleared), 1) if len(cleared) == len(other_runs) else None}
    return report


DODGE_MODES = ("auto", "dodge", "tank")
MEGA_MODES = ("off", "evolved", "all")


def recommend(reference: Reference, entries: list[dict], species: str, tier: str, *args,
              dodge: str | bool = "auto", mega: str = "off", include_mega: bool | None = None,
              compare_mega: bool = True, **kwargs) -> dict:
    """The solo raid recommendation with the dodge choice, and how mega evolution would change it.

    mega: "off" (no mega), "evolved" (one individual that has mega evolved before may join as its mega)
    or "all" (any individual that can). include_mega=True is the older name for "all". With
    compare_mega the other modes are calculated too and reported in mega_comparison."""
    if include_mega and mega == "off":
        mega = "all"
    if mega not in MEGA_MODES:
        raise RaidError("메가진화 방식을 다시 선택하세요.")
    report = _recommend_dodge(reference, entries, species, tier, *args, dodge=dodge, mega=mega, **kwargs)
    report["mega_mode"] = mega
    if compare_mega and report["result"] and reference.temp_boosts:
        report["mega_comparison"] = _mega_comparison(reference, entries, species, tier, args, kwargs, mega, report)
    return report


def _mega_member(report: dict) -> dict | None:
    return next((m for m in report["party"] if m.get("mega")), None)


def _mega_pool(reference: Reference, entries: list[dict]) -> list[dict]:
    """Box entries that can join as a mega (usable in at least one mega / primal form)."""
    pool = []
    for entry in entries:
        kinds = (reference.temp_stats or {}).get(reference.korean_names.get((entry.get("species") or "").strip()), {})
        if any(attacker(reference, entry, kind)[0] is not None for kind in kinds):
            pool.append(entry)
    return pool


def _mega_comparison(reference, entries, species, tier, args, kwargs, mode, chosen) -> dict:
    """The recommendation in every mega mode, with the same dodge choice. Runs that cannot differ are
    reused: when the best party with any mega uses one that has mega evolved before (or none), it is
    also the best with mega-evolved individuals only, and with no mega member it is the plain party."""
    pool = _mega_pool(reference, entries)
    counts = {"all": len(pool), "evolved": sum(1 for entry in pool if mega_evolved(entry))}
    outcomes = {mode: chosen}

    def run(mega):
        return _recommend(reference, entries, species, tier, *args,
                          **{**kwargs, "mega": mega, "dodge": chosen["dodge"], "compare_shadow": False})

    if counts["all"]:
        if "all" not in outcomes:
            same = mode == "evolved" and counts["all"] == counts["evolved"]
            outcomes["all"] = outcomes["evolved"] if same else run("all")
        member = _mega_member(outcomes["all"])
        if "evolved" not in outcomes:
            outcomes["evolved"] = (outcomes["all"] if member is None or member["mega"]["evolved"]
                                   else run("evolved") if counts["evolved"] else None)
        if "off" not in outcomes:
            outcomes["off"] = outcomes["all"] if member is None else run("off")
    else:
        outcomes.setdefault("off", chosen)

    def brief(report):
        if report is None or not report["result"]:
            return None
        r, member = report["result"], _mega_member(report)
        info = (member or {}).get("mega") or {}
        return {"member": member["name"] if member else None, "code": (member or {}).get("code"),
                "evolved": info.get("evolved"),
                "level": info.get("level"), "plus_move": info.get("plus_move"),
                "plus_confirmed": info.get("plus_confirmed"), "damage": r["damage"]["avg"],
                "clear_s": (r.get("clear_s") or {}).get("avg"), "defeated_scenarios": r["defeated_scenarios"],
                "party": [m["name"] for m in report["party"]]}

    return {"mode": mode, "candidates": counts, "options": {m: brief(outcomes.get(m)) for m in MEGA_MODES}}


def _recommend_dodge(reference: Reference, entries: list[dict], species: str, tier: str, *args,
                     dodge: str | bool = "auto", **kwargs) -> dict:
    """dodge: "dodge" (boss charged attacks dodged), "tank" (every attack taken), or "auto": both are
    calculated and tanking is recommended only when it does better before time runs out (more boss
    movesets cleared, or more damage where the boss survives). When both clear, dodging stays the
    recommendation and the time tanking would save is reported."""
    mode = {True: "dodge", False: "tank"}.get(dodge, dodge) if isinstance(dodge, bool) else dodge
    if mode not in DODGE_MODES:
        raise RaidError("회피 방식을 다시 선택하세요.")
    if mode != "auto":
        report = _recommend(reference, entries, species, tier, *args, dodge=mode == "dodge", **kwargs)
        report["dodge_mode"] = mode
        return report
    dodging = _recommend(reference, entries, species, tier, *args, dodge=True, **kwargs)
    if not dodging["result"]:
        dodging["dodge_mode"] = "auto"
        return dodging
    tanking = _recommend(reference, entries, species, tier, *args, dodge=False, **kwargs)

    def outcome(report):
        return _run_rank([{"defeated": s["defeated"], "damage": s["damage"], "end_ms": s["battle_s"] * 1000}
                          for s in report["by_scenario"]])

    tank_better = outcome(tanking)[:2] > outcome(dodging)[:2]  # clears or damage, not just a faster clear
    chosen, other = (tanking, dodging) if tank_better else (dodging, tanking)
    r = other["result"]
    chosen["dodge_mode"] = "auto"
    chosen["dodge_comparison"] = {
        "chosen": "tank" if tank_better else "dodge",
        "other": {"mode": "dodge" if tank_better else "tank", "damage": r["damage"], "clear_s": r["clear_s"],
                  "defeated_scenarios": r["defeated_scenarios"], "party": [m["name"] for m in other["party"]]},
    }
    return chosen


# Shadow raid enrage, community estimates (the official help gives no numbers): enraged around 60 %
# HP left, Attack about x1.81, Defense about x3. They are inputs, shown in every result.
SHADOW_DEFAULTS = {"enrage_at": 0.6, "attack": 1.81, "defense": 3.0}
SHADOW_SOURCES = ["https://scopelyexplore.helpshift.com/hc/en/5-pokemon-go/faq/1187-what-are-shadow-raids/?hl=en",
                  "https://bulbapedia.bulbagarden.net/wiki/Shadow_Raid"]


def _shadow_settings(raw: dict, tier: str, temp: str | None) -> dict:
    if tier not in ("1", "3", "5"):
        raise RaidError("그림자 레이드는 1성·3성·5성만 계산합니다.")
    if temp:
        raise RaidError("그림자 레이드 보스는 메가진화하지 않습니다.")

    def number(key, low, high, label):
        value = raw.get(key, SHADOW_DEFAULTS.get(key))
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise RaidError(f"{label}을(를) 숫자로 입력하세요.") from None
        if not low <= value <= high:
            raise RaidError(f"{label}은(는) {low:g}~{high:g} 사이로 입력하세요.")
        return value

    calm = raw.get("calm_after_s")
    calm = number("calm_after_s", 0, 600, "진정 시점") if calm not in (None, "") else None
    return {"enrage_at": number("enrage_at", 0.05, 0.95, "격앙 시점(남은 HP 비율)"),
            "attack": number("attack", 1, 5, "격앙 공격 배율"),
            "defense": number("defense", 1, 6, "격앙 방어 배율"),
            "calm_after_ms": None if calm is None else int(calm * 1000)}


def _shortlist(candidates: list[dict], per_measure: int | None = None, limit: int | None = None) -> list[dict]:
    """The candidates worth combining: the best by damage per second and the best by total damage
    before fainting (a bulky Pokémon can outlast a faster one), without duplicates."""
    per_measure = per_measure or CANDIDATE_LIMIT
    limit = limit or SHORTLIST_LIMIT
    total = lambda c: sum(d for d, _ in c["solo"])
    picked = []
    for candidate in (sorted(candidates, key=lambda c: c["dps"], reverse=True)[:per_measure]
                      + sorted(candidates, key=total, reverse=True)[:per_measure]):
        if candidate not in picked:
            picked.append(candidate)
    return picked[:limit]


def compare_weather(reference: Reference, entries: list[dict], species: str, tier: str, **options) -> list[dict]:
    """The same request in every weather, strongest first, with the party each weather picks."""
    rows = []
    for weather, name in WEATHER_NAMES.items():
        result = recommend(reference, entries, species, tier,
                           **{**options, "weather": weather, "compare_mega": False, "compare_shadow": False})
        if result["result"]:
            r = result["result"]
            rows.append({"weather": weather, "name": name, "damage": r["damage"], "clear_s": r["clear_s"],
                         "defeated_scenarios": r["defeated_scenarios"],
                         "party": [member["name"] for member in result["party"]],
                         "_rank": _run_rank([{"defeated": s["defeated"], "damage": s["damage"],
                                              "end_ms": s["battle_s"] * 1000} for s in result["by_scenario"]])})
    rows.sort(key=lambda row: row["_rank"], reverse=True)
    for row in rows:
        del row["_rank"]
    return rows


def _theoretical_dps(reference: Reference, fighter: Fighter, choice: int, foe: dict, weather: str) -> float:
    """Damage per second of one fast/charged cycle against the boss, ignoring the boss's attacks."""
    weather_types = (reference.weather or {}).get(weather, []) if weather else []
    fast, charged = fighter.fast, fighter.charged[choice]
    fast_damage = damage(reference, fast, fighter.attack, foe["defense"], fighter.types, foe["types"], weather_types,
                         mega_bonus(fighter, fast))
    charged_damage = damage(reference, charged, fighter.attack, foe["defense"], fighter.types, foe["types"],
                            weather_types, mega_bonus(fighter, charged))
    count = math.ceil(-charged.energy / max(fast.energy, 1))
    return (count * fast_damage + charged_damage) / ((count * fast.duration_ms + charged.duration_ms) / 1000)


def _summary(values: list[float]) -> dict:
    return {"avg": round(sum(values) / len(values), 1), "min": round(min(values), 1), "max": round(max(values), 1)}


def _dodge_note(reference: Reference, dodge: bool) -> str:
    if not dodge:
        return "회피하지 않습니다. 보스의 노말어택과 스페셜어택을 모두 맞습니다."
    share, wait, known = dodge_settings(reference)
    return (f"보스 노말어택은 모두 맞고, 스페셜어택은 모두 피합니다. 피하면 피해의 {share * 100:g}%만 받고, "
            f"피하는 동안 {wait / 1000:g}초 공격하지 못합니다"
            + ("(게임 전투 설정 값)." if known else
               "(게임 전투 설정의 일반 값. `python -m boxscan reference rebuild`를 실행하면 받은 기준 데이터에서 읽습니다).")
            + " 피할 타이밍을 기다리느라 실제로는 더 잃을 수 있습니다.")


def _boss_moves(foe: dict) -> dict:
    """The moves the boss can use (unknown until the battle starts), for display."""
    return {slot: [{"name": m.name, "type": TYPE_NAMES.get(m.type, m.type)} for m in foe[slot]]
            for slot in ("fast", "charged")}


def _charged_note(fighter: Fighter, options: list[dict]) -> str | None:
    if len(options) < 2:
        return None
    best, other = options[0], options[1]
    name = fighter.charged[other["index"]].name
    if best["clear_s"] is not None and other["clear_s"] is not None:
        return f"{name}보다 혼자 싸울 때 {other['clear_s'] - best['clear_s']:.1f}초 빨리 쓰러뜨려 선택"
    if best["clear_s"] is not None:
        return f"{name}로는 혼자 쓰러뜨리지 못하는 보스 기술이 있어 선택"
    return f"{name}보다 혼자 싸울 때 평균 피해가 {best['damage'] - other['damage']:.0f} 많아 선택"


def _report(reference, foe, limit_s, weather, rejoin_delay_s, scenarios, team, runs, excluded, search,
            shortfall=False, mega="off", dodge=True) -> dict:
    shadow = foe.get("shadow")
    shadow_notes = [] if not shadow else [
        f"그림자 레이드: 보스 HP가 {shadow['enrage_at'] * 100:.0f}% 남으면 격앙해 공격 ×{shadow['attack']:g}, 방어 ×{shadow['defense']:g}가 됩니다. "
        "공식 안내에는 수치가 없어 커뮤니티 추정값을 기본으로 쓰며, 화면에서 바꿀 수 있습니다.",
        "격앙 시점은 내 피해만으로 계산합니다. 다른 트레이너가 함께 때리면 더 빨리 격앙합니다.",
        ("정화 크리스탈로 진정하지 않는 경우입니다." if shadow.get("calm_after_ms") is None
         else f"격앙 {shadow['calm_after_ms'] / 1000:g}초 뒤 정화 크리스탈로 진정한다고 보고 계산했습니다(사용자가 정한 값)."),
        "격앙 중 HP 회복과 그림자 보스 자체의 공격 보정은 넣지 않았습니다.",
    ]
    party = []
    for position, candidate in enumerate(team):
        fighter, choice = candidate["fighter"], candidate["choice"]
        member_runs = [r["members"][position] for r in runs]
        note = _charged_note(fighter, candidate["options"])
        party.append({
            "order": position + 1, "id": fighter.key, **fighter.info, "name": fighter.name,
            "types": [TYPE_NAMES.get(t, t) for t in fighter.types],
            "fast": fighter.fast.name, "charged": fighter.charged[choice].name,
            "charged_type": TYPE_NAMES.get(fighter.charged[choice].type), "charged_note": note,
            "damage": _summary([m["damage"] for m in member_runs]),
            "alive_s": _summary([m["time_ms"] / 1000 for m in member_runs]),
            "theoretical_dps": round(_theoretical_dps(reference, fighter, choice, foe, weather), 1),
        })
        if fighter.info.get("mega"):
            info = fighter.info["mega"]
            party[-1]["mega"] = info | {"boost_types": [TYPE_NAMES.get(t, t) for t in info["boost_types"]]}
    result = None
    if runs:
        cleared = [r["end_ms"] / 1000 for r in runs if r["defeated"]]
        result = {
            "damage": _summary([r["damage"] for r in runs]),
            "boss_hp": foe["hp"],
            "defeated_scenarios": len(cleared),
            # Clear time over the boss movesets where the party defeats the boss.
            "clear_s": _summary(cleared) if cleared else None,
            "battle_s": _summary([r["end_ms"] / 1000 for r in runs]),
            "faints": _summary([r["faints"] for r in runs]),
            "rejoins": _summary([r["rejoins"] for r in runs]),
            "dps": _summary([r["damage"] / max(r["end_ms"] / 1000, 1) for r in runs]),
        }
    return {
        "engine": ENGINE_VERSION, "ruleset": RULESET["version"], "reference": reference.version,
        "boss": {k: foe[k] for k in ("species", "form_name", "temp", "tier", "tier_name", "hp")} | {"shadow": bool(shadow)}
        | {"types": [TYPE_NAMES.get(t, t) for t in foe["types"]],
           "attack": round(foe["attack"], 1), "defense": round(foe["defense"], 1), "moves": _boss_moves(foe)},
        "weather": weather, "weather_name": WEATHER_NAMES[weather],
        "time_limit_s": limit_s, "rejoin_delay_s": rejoin_delay_s, "dodge": dodge,
        "scenarios": [{"fast": f.name, "charged": c.name} for f, c in scenarios],
        "by_scenario": [{"fast": f.name, "charged": c.name, "damage": r["damage"], "defeated": r["defeated"],
                         "battle_s": r["end_ms"] / 1000} for (f, c), r in zip(scenarios, runs)],
        "party": party, "result": result, "excluded": excluded, "search": search,
        "shortfall": (f"추천에 쓸 수 있는 개체가 {len(team)}마리라 6마리를 채우지 못했습니다." if shortfall else None),
        "assumptions": [
            "혼자 하는 레이드 기준 순위: 보스를 쓰러뜨리는 파티가 먼저이고, 쓰러뜨리는 파티끼리는 처치 시간이 짧은 순, "
            "못 쓰러뜨리면 준 피해가 큰 순입니다. 보스 기술은 들어가기 전에 모르므로 가능한 조합을 모두 계산해 비교합니다.",
            "전원 체력 가득·에너지 0으로 시작합니다.",
            _dodge_note(reference, dodge),
            "스페셜어택은 에너지가 모이면 바로 씁니다. 두 스페셜어택 중 한 가지만 계속 씁니다.",
            "보스는 기술 사이에 평균 2초를 쉬고, 스페셜어택은 쓸 수 있을 때 약 절반만 씁니다(번갈아 사용으로 근사).",
            "보스 기술을 정하지 않으면 가능한 조합을 모두 계산해 같은 비중으로 평균합니다. 실제 확률이 아닙니다.",
            ("재입장 없음: 6마리가 모두 기절하면 내 피해는 거기서 끝납니다." if rejoin_delay_s is None
             else f"재입장: 모두 기절하면 {rejoin_delay_s:g}초 뒤 같은 파티로 다시 들어갑니다(사용자가 정한 값)."),
            "날씨 보너스는 내 기술과 보스 기술 모두에 같은 규칙(해당 타입 ×1.2)으로 적용합니다.",
            "박스에 그림자로 표시한 개체는 공격 ×1.2, 방어 ×0.833(게임 전투 설정 값)을 적용합니다. 표시하지 않은 개체는 일반 개체로 계산합니다.",
            *shadow_notes,
            *([_MEGA_MODE_NOTES[mega], *_MEGA_ASSUMPTIONS] if mega != "off" else [_MEGA_MODE_NOTES["off"]]),
            "친구·파티 파워·다른 트레이너의 메가 보너스는 넣지 않았습니다.",
            f"보스 HP·CP 배율은 {RULESET['version']} 표를 쓰고, 제한 시간 기본값은 앱이 정한 시나리오 값입니다.",
        ],
    }


_MEGA_MODE_NOTES = {
    "off": "메가진화는 넣지 않았습니다. 메가진화를 넣었을 때 얼마나 달라지는지는 결과에 따로 비교합니다.",
    "evolved": "메가진화: 메가진화해 본 개체(메가 상태로 스캔했거나 메가 레벨이 있거나 직접 표시한 개체)만 그 모습으로도 후보에 넣습니다. "
               "한 번 메가진화한 개체는 언제든 다시 할 수 있다고 보고, 메가에너지·재사용 대기 시간은 따지지 않습니다.",
    "all": "메가진화: 메가진화·원시회귀할 수 있는 개체는 아직 메가진화해 보지 않았어도 그 모습으로 후보에 넣습니다. 메가에너지는 충분하다고 가정합니다.",
}
_MEGA_ASSUMPTIONS = [
    "메가진화 개체는 그 모습(종족값·타입)으로 계산하고 파티에는 최대 1마리만 넣습니다.",
    "메가진화한 개체가 싸우는 동안 그 개체의 공격은 같은 타입 ×1.3, 다른 타입 ×1.1입니다(게임 마스터 값). 원시그란돈·원시가이오가·메가레쿠쟈는 게임 마스터에 적힌 세 타입이 ×1.3입니다.",
    "혼자 싸우면 다른 내 포켓몬은 대기 중이라 메가 보너스를 받지 않습니다. 메가 개체 자신의 공격에도 보너스가 붙는다는 점은 커뮤니티 자료 기준입니다.",
    "메가 레벨이 달라도 메가 보너스(×1.3·×1.1)는 같습니다. 게임 데이터에 '+' 스페셜어택이 있는 메가는 그 기술을 쓸 수 있다고 보고 계산합니다"
    "(메가 상태로 스캔해 화면에서 확인하지 않았으면 가정으로 표시, 내 박스 수정에서 끌 수 있음). 위력 배율은 저장한 메가 레벨을 쓰고, "
    "메가 레벨이 없으면 쓸 수 있는 가장 낮은 레벨의 배율로 계산합니다.",
]

"""Normal raid recommendation (spec v1.2 sections 7 and 8.1).

The goal is the most expected damage my party deals to the boss before the time limit ends, the
boss falls, or (without re-entry) everyone faints. The engine is a deterministic event simulation:

- Damage: floor(0.5 * power * attack / defense * STAB * weather * type) + 1, values from the game
  master. Attack and defense are (base + IV) * CPM for my Pokémon and (base + 15) * tier CPM for
  the boss; boss HP comes from the tier table below.
- My Pokémon start at full HP with 0 energy, never dodge, use the fast attack until the chosen
  charged attack is affordable, then use it. Damage lands at the move's damage window. Energy comes
  from my fast attacks and from damage taken (0.5 per HP). A fainted Pokémon's pending hit is lost.
  The next one enters after the swap time.
- The boss uses a move, then waits enemyAttackInterval + 0.5 s (the midpoint of the 1.5–2.5 s
  spread). It uses its charged attack on every second chance once it has the energy (about 50 %).
  It gains energy from its moves and from damage taken.
- Boss movesets that are not chosen are all simulated and reported as an even average with the
  lowest and highest result. That is a scenario average, not the game's real probabilities.

Party search: every eligible Pokémon picks the charged attack that deals more damage in a solo run;
the strongest candidates (by damage per second) are kept, every party of up to six from them is
estimated from the solo runs, and the best few are simulated in full. The search is exhaustive over
the kept candidates, not over every possible action, and the result says so.

Mega evolution (optional): every box Pokémon that can mega evolve or reverse into a primal form is
also tried in that form (its stats and types, and its "+" move when the box has it). A party holds
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
from .species import TEMP_NAMES

ENGINE_VERSION = "raid-sim/2"
PARTY_SIZE = 6
CANDIDATE_LIMIT = 12  # best by each measure (damage per second, total damage)
SHORTLIST_LIMIT = 18  # at most this many are combined (18 choose 6 = 18,564 parties)
MEGA_CANDIDATE_LIMIT = 4
FULL_SIM_PARTIES = 5
BOSS_EXTRA_DELAY_MS = 500

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
        plus = _mega_plus(reference, entry, stats.pokemon_id, temp)
        if plus:
            charged = [*charged, plus]
        info["mega"] = {"kind": temp, "name": TEMP_NAMES.get(temp, temp), "boost_types": boosted,
                        "same_type": boosts.get("same_type", 1.0), "different_type": boosts.get("different_type", 1.0),
                        "plus_move": plus.name if plus else None}
    return Fighter(entry["id"], name, attack, defense, int(hp), types, fast, charged, info), None


def _mega_plus(reference: Reference, entry: dict, pokemon_id: int, temp: str) -> Move | None:
    """The mega's "+" charged attack, when the box recorded it for this form. The mega level is not
    stored, so the lowest level that unlocks it is assumed (multiplier from the game master)."""
    recorded = (entry.get("mega_move") or {}).get("move_id")
    data = (reference.mega_moves or {}).get(pokemon_id, {}).get(temp) or {}
    if not recorded or recorded != data.get("move_id"):
        return None
    move = _move(reference, recorded)
    if move is None:
        return None
    levels = (reference.temp_boosts or {}).get("special_move") or [1.0]
    move.name, move.bonus = data.get("name") or move.name, levels[min(1, len(levels) - 1)]
    return move


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


def simulate(reference: Reference, party: list[tuple[Fighter, int]], foe: dict, boss_fast: Move,
             boss_charged: Move, time_limit_s: float, weather: str = "", rejoin_delay_s: float | None = None,
             trainers: int = 1) -> dict:
    """Run one battle. party = [(fighter, index of the charged attack to use)], in order.

    With trainers > 1 the others are assumed to deal as much as I do at the same moments, so the boss
    loses HP that many times faster; only my share is counted as my damage."""
    weather_types = (reference.weather or {}).get(weather, []) if weather else []
    settings = reference.battle
    limit = int(time_limit_s * 1000)
    max_energy = settings["maximumEnergy"]
    swap = settings["swapDurationMs"]
    boss_delay = int(settings["enemyAttackInterval"] * 1000) + BOSS_EXTRA_DELAY_MS
    shadow = foe.get("shadow")

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
                current, hp, energy, entered = following, party[following][0].hp, 0, start
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


def _estimate(members: list[list[tuple[int, int]]], limit_ms: int, swap_ms: int, rejoin_ms: int | None) -> float:
    """Party damage from solo runs (damage, alive ms) per member, ordered strongest first."""
    remaining, total = limit_ms, 0.0
    while remaining > 0:
        for dealt, alive in members:
            if remaining <= 0:
                break
            if alive >= remaining:
                total += dealt * remaining / max(alive, 1)
                remaining = 0
                break
            total += dealt
            remaining -= alive + swap_ms
        if rejoin_ms is None:
            break
        remaining -= rejoin_ms - swap_ms
    return total


def recommend(reference: Reference, entries: list[dict], species: str, tier: str, form: str | None = None,
              temp: str | None = None, weather: str = "", time_limit_s: float | None = None,
              rejoin_delay_s: float | None = None, boss_fast: str | None = None,
              boss_charged: str | None = None, include_mega: bool = False, compare_mega: bool = True,
              shadow: dict | None = None, compare_shadow: bool = True, trainers: int = 1) -> dict:
    if include_mega and not reference.temp_boosts:
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
            runs = [simulate(reference, [(fighter, index)], foe, f, c, limit_s, weather, trainers=trainers)
                    for f, c in scenarios]
            solo = [(r["damage"], max(r["members"][0]["time_ms"], 1)) for r in runs]
            options.append((sum(d for d, _ in solo) / len(solo), index, solo))
        options.sort(key=lambda option: option[0], reverse=True)
        best_damage, choice, solo = options[0]
        dps = sum(d / t for d, t in solo) / len(solo) * 1000
        return {"fighter": fighter, "choice": choice, "solo": solo, "dps": dps,
                "options": [(o[1], o[0]) for o in options]}

    excluded, candidates, megas = [], [], []
    for entry in entries:
        fighter, reason = attacker(reference, entry)
        if fighter is None:
            excluded.append({"code": entry.get("code"), "name": entry.get("species") or "종 미확인", "reason": reason})
            continue
        candidates.append(candidate(fighter))
        if include_mega:
            pokemon_id = reference.korean_names.get(entry["species"].strip())
            for kind in (reference.temp_stats or {}).get(pokemon_id, {}):
                mega, _ = attacker(reference, entry, kind)
                if mega is not None:
                    megas.append(candidate(mega))
    if not candidates:
        return _report(reference, foe, limit_s, weather, rejoin_delay_s, scenarios, [], [], excluded,
                       {"candidates": 0, "considered": 0, "combinations": 0, "approximate": False,
                        "mega_candidates": 0}, include_mega=include_mega)

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
    combos = [team for team in itertools.combinations(considered, size) if allowed(team)]
    for team in combos:
        team = ordered(team)
        score = sum(_estimate([c["solo"][s] for c in team], int(limit_s * 1000), swap, rejoin_ms)
                    for s in range(len(scenarios))) / len(scenarios)
        estimates.append((score, team))
    estimates.sort(key=lambda item: item[0], reverse=True)

    best = None
    for _, team in estimates[:FULL_SIM_PARTIES]:
        runs = [simulate(reference, [(c["fighter"], c["choice"]) for c in team], foe, f, ch, limit_s, weather,
                         rejoin_delay_s, trainers) for f, ch in scenarios]
        average = sum(r["damage"] for r in runs) / len(runs)
        if best is None or average > best[0]:
            best = (average, team, runs)
    _, team, runs = best
    search = {"candidates": len(candidates), "considered": len(considered), "combinations": len(combos),
              "fully_simulated": min(FULL_SIM_PARTIES, len(estimates)), "mega_candidates": len(megas),
              "approximate": len(candidates) > len(considered) - len([c for c in considered if c in megas])
              or len(megas) > MEGA_CANDIDATE_LIMIT}
    report = _report(reference, foe, limit_s, weather, rejoin_delay_s, scenarios, team, runs, excluded, search,
                     shortfall=len(candidates) < PARTY_SIZE, include_mega=include_mega)
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
                                   weather, rejoin_delay_s, trainers) for f, ch in scenarios]
            report["shadow"]["comparison"] = {
                "calm_after_s": None if other["calm_after_ms"] is None else other["calm_after_ms"] / 1000,
                "damage": round(sum(r["damage"] for r in other_runs) / len(other_runs), 1)}
    if include_mega and compare_mega:
        plain = recommend(reference, entries, species, tier, form, temp, weather, time_limit_s, rejoin_delay_s,
                          boss_fast, boss_charged, shadow=shadow, compare_shadow=False, trainers=trainers)
        chosen = next((m for m in report["party"] if m["mega"]), None)
        report["mega_comparison"] = {
            "with_mega": report["result"]["damage"]["avg"], "without_mega": plain["result"]["damage"]["avg"],
            "member": chosen["name"] if chosen else None, "candidates": len(megas)}
    return report


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
            rows.append({"weather": weather, "name": name, "damage": result["result"]["damage"],
                         "party": [member["name"] for member in result["party"]]})
    return sorted(rows, key=lambda row: row["damage"]["avg"], reverse=True)


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


def _report(reference, foe, limit_s, weather, rejoin_delay_s, scenarios, team, runs, excluded, search,
            shortfall=False, include_mega=False) -> dict:
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
        options = candidate["options"]
        note = None
        if len(options) > 1:
            (_, best_damage), (other_index, other_damage) = options[0], options[1]
            note = (f"{fighter.charged[other_index].name}보다 혼자 싸울 때 평균 피해가 "
                    f"{best_damage - other_damage:.0f} 많아 선택")
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
            mega = fighter.info["mega"]
            party[-1]["mega"] = mega | {"boost_types": [TYPE_NAMES.get(t, t) for t in mega["boost_types"]]}
    result = None
    if runs:
        result = {
            "damage": _summary([r["damage"] for r in runs]),
            "boss_hp": foe["hp"],
            "defeated_scenarios": sum(1 for r in runs if r["defeated"]),
            "battle_s": _summary([r["end_ms"] / 1000 for r in runs]),
            "faints": _summary([r["faints"] for r in runs]),
            "rejoins": _summary([r["rejoins"] for r in runs]),
            "dps": _summary([r["damage"] / max(r["end_ms"] / 1000, 1) for r in runs]),
        }
    return {
        "engine": ENGINE_VERSION, "ruleset": RULESET["version"], "reference": reference.version,
        "boss": {k: foe[k] for k in ("species", "form_name", "temp", "tier", "tier_name", "hp")} | {"shadow": bool(shadow)}
        | {"types": [TYPE_NAMES.get(t, t) for t in foe["types"]],
           "attack": round(foe["attack"], 1), "defense": round(foe["defense"], 1)},
        "weather": weather, "weather_name": WEATHER_NAMES[weather],
        "time_limit_s": limit_s, "rejoin_delay_s": rejoin_delay_s,
        "scenarios": [{"fast": f.name, "charged": c.name} for f, c in scenarios],
        "by_scenario": [{"fast": f.name, "charged": c.name, "damage": r["damage"], "defeated": r["defeated"],
                         "battle_s": r["end_ms"] / 1000} for (f, c), r in zip(scenarios, runs)],
        "party": party, "result": result, "excluded": excluded, "search": search,
        "shortfall": (f"추천에 쓸 수 있는 개체가 {len(team)}마리라 6마리를 채우지 못했습니다." if shortfall else None),
        "assumptions": [
            "전원 체력 가득·에너지 0으로 시작하고 회피하지 않습니다.",
            "스페셜어택은 에너지가 모이면 바로 씁니다. 두 스페셜어택 중 한 가지만 계속 씁니다.",
            "보스는 기술 사이에 평균 2초를 쉬고, 스페셜어택은 쓸 수 있을 때 약 절반만 씁니다(번갈아 사용으로 근사).",
            "보스 기술을 정하지 않으면 가능한 조합을 모두 계산해 같은 비중으로 평균합니다. 실제 확률이 아닙니다.",
            ("재입장 없음: 6마리가 모두 기절하면 내 피해는 거기서 끝납니다." if rejoin_delay_s is None
             else f"재입장: 모두 기절하면 {rejoin_delay_s:g}초 뒤 같은 파티로 다시 들어갑니다(사용자가 정한 값)."),
            "날씨 보너스는 내 기술과 보스 기술 모두에 같은 규칙(해당 타입 ×1.2)으로 적용합니다.",
            "박스에 그림자로 표시한 개체는 공격 ×1.2, 방어 ×0.833(게임 전투 설정 값)을 적용합니다. 표시하지 않은 개체는 일반 개체로 계산합니다.",
            *shadow_notes,
            *(_MEGA_ASSUMPTIONS if include_mega else ["메가진화는 넣지 않았습니다. 켜면 박스에서 메가진화할 수 있는 개체 1마리를 메가진화 상태로 함께 비교합니다."]),
            "친구·파티 파워·다른 트레이너의 메가 보너스는 넣지 않았습니다.",
            f"보스 HP·CP 배율은 {RULESET['version']} 표를 쓰고, 제한 시간 기본값은 앱이 정한 시나리오 값입니다.",
        ],
    }


_MEGA_ASSUMPTIONS = [
    "메가진화: 메가진화·원시회귀할 수 있는 개체는 그 모습(종족값·타입)으로도 후보에 넣고, 파티에는 최대 1마리만 넣습니다. 메가에너지는 충분하다고 가정합니다.",
    "메가진화한 개체가 싸우는 동안 그 개체의 공격은 같은 타입 ×1.3, 다른 타입 ×1.1입니다(게임 마스터 값). 원시그란돈·원시가이오가·메가레쿠쟈는 게임 마스터에 적힌 세 타입이 ×1.3입니다.",
    "혼자 싸우면 다른 내 포켓몬은 대기 중이라 메가 보너스를 받지 않습니다. 메가 개체 자신의 공격에도 보너스가 붙는다는 점은 커뮤니티 자료 기준입니다.",
    "메가 레벨은 박스에 없어 모든 메가 개체를 같은 보너스로 계산합니다. 박스에 기록된 '+' 스페셜어택은 쓸 수 있는 가장 낮은 메가 레벨의 위력 배율로 계산합니다.",
]

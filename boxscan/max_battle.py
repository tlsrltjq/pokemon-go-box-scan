"""Max battle (Dynamax / Gigantamax) recommendation (spec v1.2 section 8.3).

This is a separate engine from normal raids. One trainer brings up to three Dynamax- or
Gigantamax-capable Pokémon; one is on the field at a time. The goal is the most damage those three
deal before the boss falls, the time runs out, or all three faint. Other trainers' damage and meter
are not modelled (spec: unknown teammates count as zero contribution); an optional trainer count only
splits the boss's single-target attacks among the lobby.

Battle model (max-sim/1), deterministic:
- Normal phase: my active Pokémon uses its fast attack, and its charged attack when affordable if
  that policy deals more damage (both policies are tried). Damage uses the raid formula with STAB,
  weather and type effectiveness. Energy comes from fast attacks and damage taken (0.5 per HP).
- Max Meter (0–100): every hit adds max(1, floor(damage × multiplier / (boss HP / 200))), with the
  tier multiplier below. A Max Orb adds 10 every 15 s (assumed collected). At 100 the best alive
  party member takes the field for the Max Phase: a 3 s countdown and three max moves (2.5 s each,
  damage at 2.0 s). The boss does not attack during it. Then the meter resets and the previous
  active Pokémon returns (swap time applies).
- Max moves: Max Attack (type of the fast attack) 250/300/350 power by level, G-Max moves
  350/400/450, Max Guard adds a 20/40/60 HP shield (up to 3 stacked), Max Spirit heals 8/12/16 %
  of max HP. Only moves unlocked in the box are used; Max Attack level 1 is assumed when the box
  has no max move data. Each Pokémon's three-move plan is chosen from attack-only and
  guard/spirit-first plans by the damage it leads to.
- Boss: attack and defense = (base + 15) × tier CPM, attack × tier attack multiplier. It attacks
  at a fixed interval with one of its charged attacks, alternating a single-target attack (×2
  damage when not dodged; no dodging is assumed) and a spread attack (×1).

Tier values come from community sources (see RULESET) and are scenario values, not game data.
Enrage after a few minutes is not modelled.
"""
import heapq
import itertools
import math
from dataclasses import dataclass, field

from . import level as level_rules
from .forms import display_name
from .raid import TYPE_NAMES, WEATHER_NAMES, Move, RaidError, _move, _types, damage
from .reference import Reference

ENGINE_VERSION = "max-sim/1"
PARTY_SIZE = 3
CANDIDATE_LIMIT = 10
PHASE_COUNTDOWN_MS = 3000
MAX_MOVE_MS, MAX_MOVE_HIT_MS = 2500, 2000
ORB_INTERVAL_MS, ORB_ENERGY = 15_000, 10
TARGETED_MULTIPLIER = 2.0
MAX_ATTACK_POWER = {1: 250, 2: 300, 3: 350}
GMAX_POWER = {1: 350, 2: 400, 3: 450}
GUARD_HP = {1: 20, 2: 40, 3: 60}
SPIRIT_HEAL = {1: 0.08, 2: 0.12, 3: 0.16}
MAX_SHIELDS = 3

RULESET = {
    "version": "max-ruleset/2026-11-17",
    # hp, cpm, attack multiplier, meter multiplier, boss attack interval (s), time limit (s)
    "tiers": {
        "1": {"name": "1성", "hp": 1700, "cpm": 0.15, "attack": 1.0, "meter": 1, "interval": 7.5, "time_limit": 480},
        "2": {"name": "2성", "hp": 5000, "cpm": 0.38, "attack": 1.0, "meter": 1, "interval": 7.5, "time_limit": 480},
        "3": {"name": "3성", "hp": 10000, "cpm": 0.5, "attack": 1.0, "meter": 1, "interval": 7.5, "time_limit": 480},
        "4": {"name": "4성", "hp": 20000, "cpm": 0.6, "attack": 1.0, "meter": 1, "interval": 7.5, "time_limit": 480},
        "5": {"name": "5성 (전설 다이맥스)", "hp": 17500, "cpm": 0.7, "attack": 1.0, "meter": 4, "interval": 7.5,
              "time_limit": 400},
        "gmax": {"name": "거다이맥스", "hp": 100000, "cpm": 0.85, "attack": 1.0, "meter": 15, "interval": 4.0,
                 "time_limit": 600},
    },
    "sources": [
        "https://bulbapedia.bulbagarden.net/wiki/Max_Battle",
        "https://pokechespin.net/whatsnew",
        "https://mattsimplified.com/guide/max-battles/",
        "https://www.dittobase.com/pokemon-go/moves/g-max-stun-shock",
    ],
}

PLANS = {"attack": ("attack", "attack", "attack"), "guard": ("guard", "attack", "attack"),
         "spirit": ("spirit", "attack", "attack")}
PLAN_NAMES = {"attack": "맥스어택 3번", "guard": "다이월 1번 + 맥스어택 2번", "spirit": "다이리커버리 1번 + 맥스어택 2번"}


@dataclass
class MaxFighter:
    key: str
    name: str
    attack: float
    defense: float
    hp: int
    types: list[str]
    fast: object
    charged: list
    max_attack: dict  # {"type", "power", "name", "level", "gmax"}
    guard: int | None = None  # level
    spirit: int | None = None
    info: dict = field(default_factory=dict)


def boss(reference: Reference, species: str, tier: str, form: str | None = None) -> dict:
    if tier not in RULESET["tiers"]:
        raise RaidError("맥스배틀 등급을 다시 선택하세요.")
    options = level_rules._stat_options(reference, species, None, form)
    if not options:
        raise RaidError("기준 데이터에서 보스를 찾지 못했습니다.")
    stats, rule = options[0], RULESET["tiers"][tier]
    learnset = (reference.learnsets or {}).get(f"{stats.pokemon_id}|{form or 'Normal'}") \
        or (reference.learnsets or {}).get(f"{stats.pokemon_id}|Normal") or {}
    charged = [m for m in (_move(reference, i) for i in learnset.get("charged", [])) if m]
    if not charged:
        raise RaidError("보스의 스페셜어택 데이터를 찾지 못했습니다.")
    return {"species": species, "form_name": display_name(stats.pokemon_id, form), "tier": tier,
            "tier_name": rule["name"], "hp": rule["hp"], "attack": (stats.attack + 15) * rule["cpm"] * rule["attack"],
            "defense": (stats.defense + 15) * rule["cpm"], "types": _types(reference, stats.pokemon_id, form),
            "charged": charged, "rule": rule}


def species_move(reference: Reference, pokemon_id: int, form: str | None) -> str | None:
    """The species' own G-Max (or special Max) move id from the game master, if it has one."""
    found = reference.species_max_moves or {}
    return found.get(f"{pokemon_id}|{form or 'Normal'}") or found.get(f"{pokemon_id}|*") \
        or found.get(f"{pokemon_id}|Normal")


def _max_kind(name: str) -> str:
    if name == "다이월":
        return "guard"
    if name == "다이리커버리":
        return "spirit"
    return "gmax" if name.startswith("거다이") else "attack"


def fighter(reference: Reference, entry: dict) -> tuple[MaxFighter | None, str | None]:
    """A box entry as a max battle fighter, or why it cannot join."""
    state = entry.get("max_state")
    if state not in ("dynamax", "gigantamax"):
        return None, "다이맥스·거다이맥스 개체가 아님"
    species, iv, lvl = entry.get("species"), entry.get("iv"), (entry.get("level") or {}).get("value")
    if not species or not reference.forms(species):
        return None, "종을 기준 데이터에서 찾지 못함"
    if not iv:
        return None, "IV 없음"
    if lvl is None:
        return None, "레벨이 하나로 정해지지 않음"
    form = entry.get("form") or "Normal"
    options = level_rules._stat_options(reference, species, None, form)
    if not options:
        return None, "모습의 종족값을 찾지 못함"
    stats = options[0]
    moves = entry.get("moves") or {}
    fast = _move(reference, (moves.get("fast") or {}).get("move_id"))
    if fast is None:
        return None, "노말어택을 기술 사전과 연결하지 못함"
    charged = [m for m in (_move(reference, (c or {}).get("move_id")) for c in moves.get("charged") or []) if m]
    cpm = reference.cpm.get(float(lvl))
    if cpm is None:
        return None, "레벨의 CP 배율이 없음"
    attack, defense = (stats.attack + iv["attack"]) * cpm, (stats.defense + iv["defense"]) * cpm
    if entry.get("shadow") is True:
        attack *= reference.battle.get("shadowPokemonAttackBonusMultiplier", 1.2)
        defense *= reference.battle.get("shadowPokemonDefenseBonusMultiplier", 0.8333333)
    hp = entry.get("max_hp") or max(10, math.floor((stats.stamina + iv["stamina"]) * cpm))
    known = {_max_kind(m["name"]): m for m in entry.get("max_moves") or [] if m.get("name")}
    gmax = state == "gigantamax"
    attack_move = known.get("gmax" if gmax else "attack")
    attack_level = attack_move.get("level") if attack_move and not attack_move.get("locked") else None
    attack_level = attack_level or 1  # every Max Pokémon starts with its Max Attack at level 1
    move_id = species_move(reference, stats.pokemon_id, form)
    data = (reference.max_moves or {}).get(move_id or "")
    if not gmax and (data is None or data["kind"] != "attack"):  # Dynamax: Max Attack of the fast move type
        data = (reference.max_moves or {}).get((reference.max_move_by_type or {}).get(fast.type, ""))
    move_type = (data or {}).get("type") or fast.type
    fallback = "거다이맥스 기술" if gmax else "맥스어택"
    name = (attack_move or {}).get("name") or (data or {}).get("name") or f"{fallback} ({TYPE_NAMES.get(move_type, move_type)})"
    power = (GMAX_POWER if gmax else MAX_ATTACK_POWER)[min(attack_level, 3)]

    def level_of(kind):
        move = known.get(kind)
        return move.get("level") if move and move.get("level") and not move.get("locked") else None

    marks = "".join(mark for key, mark in (("shiny", "*"), ("lucky", "♣"), ("background", "◆")) if entry.get(key) is True)
    label = (entry.get("form_name") if form != "Normal" and entry.get("form_name") else species) + marks
    if entry.get("shadow") is True:
        label += " (그림자)"
    return MaxFighter(entry["id"], label, attack, defense, int(hp), _types(reference, stats.pokemon_id, form), fast,
                      charged, {"type": move_type, "power": power, "name": name, "level": attack_level, "gmax": gmax},
                      level_of("guard"), level_of("spirit"),
                      {"code": entry.get("code"), "revision": entry.get("revision"), "level": lvl, "iv": iv,
                       "max_state": state, "shadow": entry.get("shadow") is True}), None


def _plans(f: MaxFighter) -> list[str]:
    return ["attack"] + (["guard"] if f.guard else []) + (["spirit"] if f.spirit else [])


def simulate(reference: Reference, party: list[tuple[MaxFighter, bool, str]], foe: dict, boss_move,
             time_limit_s: float, weather: str = "", orbs: bool = True, trainers: int = 1) -> dict:
    """party = [(fighter, use charged attacks, max phase plan)]. With other trainers in the lobby a
    single-target attack hits me only in 1 of `trainers` cases; that share is applied as an expected
    value. Other trainers' damage is still not counted."""
    weather_types = (reference.weather or {}).get(weather, []) if weather else []
    settings = reference.battle
    rule = foe["rule"]
    limit = int(time_limit_s * 1000)
    interval = int(rule["interval"] * 1000)
    swap = settings["swapDurationMs"]
    per_point = foe["hp"] / 200
    stats = [{"damage": 0, "max_damage": 0, "time_ms": 0, "faints": 0, "max_phases": 0} for _ in party]

    def deal(f, move):
        return damage(reference, move, f.attack, foe["defense"], f.types, foe["types"], weather_types)

    fast_hits = [deal(f, f.fast) for f, _, _ in party]
    charged_hits = [deal(f, f.charged[0]) if f.charged else 0 for f, _, _ in party]
    max_hits = [deal(f, Move("max", f.max_attack["name"], f.max_attack["type"], f.max_attack["power"], 0,
                             MAX_MOVE_MS, MAX_MOVE_HIT_MS)) for f, _, _ in party]
    taken = [damage(reference, boss_move, foe["attack"], f.defense, foe["types"], f.types, weather_types)
             for f, _, _ in party]
    hp = [f.hp for f, _, _ in party]
    shields: list[int] = []  # Max Guard shields protect whoever is on the field
    energy = [0] * len(party)
    boss_hp, meter = foe["hp"], 0.0
    current, entered = 0, 0
    generation, targeted = 0, True
    events, order = [], itertools.count()
    end_time, defeated, phases = limit, False, 0

    def push(time, kind, data=None):
        heapq.heappush(events, (time, 0 if kind == "hit" else 1, next(order), kind, data))

    def add_meter(dealt):
        nonlocal meter
        meter = min(100.0, meter + max(1, math.floor(dealt * rule["meter"] / per_point)))

    def alive():
        return [i for i, h in enumerate(hp) if h > 0]

    push(0, "attack", generation)
    push(interval, "boss")
    if orbs:
        push(ORB_INTERVAL_MS, "orb")
    phase_until = -1
    while events:
        time, _, _, kind, data = heapq.heappop(events)
        if time >= limit or boss_hp <= 0 or not alive():
            break
        if kind == "orb":
            meter = min(100.0, meter + ORB_ENERGY)
            push(time + ORB_INTERVAL_MS, "orb")
            continue
        if kind == "boss":
            if time < phase_until:  # the boss does not attack during a Max Phase
                push(phase_until + interval, "boss")
                continue
            push(time + interval, "boss")
            single, targeted = targeted, not targeted
            if time < entered:  # nobody on the field while swapping
                continue
            dealt = int(taken[current] * (TARGETED_MULTIPLIER / trainers if single else 1.0))
            while dealt > 0 and shields:
                absorbed = min(dealt, shields[0])
                shields[0] -= absorbed
                dealt -= absorbed
                if shields[0] <= 0:
                    shields.pop(0)
            hp[current] -= dealt
            energy[current] = min(100, energy[current] + math.ceil(dealt * settings["energyDeltaPerHealthLost"]))
            if hp[current] <= 0:
                stats[current]["time_ms"] += time - entered
                stats[current]["faints"] += 1
                rest = alive()
                if not rest:
                    end_time = time
                    break
                generation += 1
                current, entered = rest[0], time + swap
                energy[current] = 0
                push(entered, "attack", generation)
            continue
        if kind == "attack":
            if data != generation or time < phase_until:
                continue
            if meter >= 100:  # Max Phase with the strongest alive member
                runner = max(alive(), key=lambda i: max_hits[i] * (3 if party[i][2] == "attack" else 2))
                plan = PLANS[party[runner][2]]
                t = time + PHASE_COUNTDOWN_MS
                for action in plan:
                    t += MAX_MOVE_MS
                    f = party[runner][0]
                    if action == "attack":
                        dealt = min(max_hits[runner], boss_hp)
                        boss_hp -= dealt
                        stats[runner]["damage"] += dealt
                        stats[runner]["max_damage"] += dealt
                        if boss_hp <= 0:
                            defeated, end_time = True, t - MAX_MOVE_MS + MAX_MOVE_HIT_MS
                            break
                    elif action == "guard":
                        if len(shields) < MAX_SHIELDS:
                            shields.append(GUARD_HP[f.guard])
                    elif action == "spirit":
                        for i in alive():
                            hp[i] = min(party[i][0].hp, hp[i] + math.floor(f.hp * SPIRIT_HEAL[f.spirit]))
                stats[runner]["max_phases"] += 1
                phases += 1
                meter = 0.0
                if defeated:
                    break
                phase_until = t
                generation += 1
                resume = t + (swap if runner != current else 0)
                push(resume, "attack", generation)
                continue
            f, use_charged, _ = party[current]
            if use_charged and f.charged and energy[current] >= -f.charged[0].energy:
                energy[current] += f.charged[0].energy
                push(time + f.charged[0].damage_ms, "hit", (generation, charged_hits[current], 0))
                push(time + f.charged[0].duration_ms, "attack", generation)
            else:
                push(time + f.fast.damage_ms, "hit", (generation, fast_hits[current], f.fast.energy))
                push(time + f.fast.duration_ms, "attack", generation)
            continue
        if kind == "hit":
            hit_generation, dealt, gained = data
            if hit_generation != generation:
                continue
            dealt = min(dealt, boss_hp)
            boss_hp -= dealt
            stats[current]["damage"] += dealt
            energy[current] = min(100, energy[current] + gained)
            add_meter(dealt)
            if boss_hp <= 0:
                defeated, end_time = True, time
    if alive():
        stats[current]["time_ms"] += min(end_time, limit) - entered
    return {"damage": sum(s["damage"] for s in stats), "defeated": defeated, "end_ms": min(end_time, limit),
            "max_phases": phases, "faints": sum(s["faints"] for s in stats), "members": stats}


def recommend(reference: Reference, entries: list[dict], species: str, tier: str, form: str | None = None,
              weather: str = "", time_limit_s: float | None = None, boss_charged: str | None = None,
              orbs: bool = True, trainers: int = 1) -> dict:
    if weather not in WEATHER_NAMES:
        raise RaidError("날씨를 다시 선택하세요.")
    if trainers not in (1, 2, 3, 4):
        raise RaidError("함께하는 트레이너 수는 1~4명입니다.")
    foe = boss(reference, species, tier, form)
    limit_s = time_limit_s or foe["rule"]["time_limit"]
    if not 60 <= limit_s <= 900:
        raise RaidError("제한 시간은 60~900초 사이로 입력하세요.")
    moves = [m for m in foe["charged"] if not boss_charged or m.move_id == boss_charged]
    if not moves:
        raise RaidError("선택한 보스 기술이 보스의 기술 목록에 없습니다.")

    excluded, candidates = [], []
    for entry in entries:
        f, reason = fighter(reference, entry)
        if f is None:
            if entry.get("max_state") in ("dynamax", "gigantamax") or reason != "다이맥스·거다이맥스 개체가 아님":
                excluded.append({"code": entry.get("code"), "name": entry.get("species") or "종 미확인", "reason": reason})
            continue
        best = None
        for use_charged in ([False, True] if f.charged else [False]):
            for plan in _plans(f):
                runs = [simulate(reference, [(f, use_charged, plan)], foe, m, limit_s, weather, orbs, trainers) for m in moves]
                average = sum(r["damage"] for r in runs) / len(runs)
                if best is None or average > best[0]:
                    best = (average, use_charged, plan)
        candidates.append({"fighter": f, "solo": best[0], "charged": best[1], "plan": best[2]})
    non_max = sum(1 for e in entries if e.get("max_state") not in ("dynamax", "gigantamax"))
    if not candidates:
        return _report(reference, foe, limit_s, weather, orbs, trainers, moves, [], [], excluded, non_max,
                       {"candidates": 0, "considered": 0, "parties": 0, "approximate": False})
    candidates.sort(key=lambda c: c["solo"], reverse=True)
    considered = candidates[:CANDIDATE_LIMIT]
    size = min(PARTY_SIZE, len(considered))
    best = None
    count = 0
    for team in itertools.permutations(considered, size):
        count += 1
        party = [(c["fighter"], c["charged"], c["plan"]) for c in team]
        runs = [simulate(reference, party, foe, m, limit_s, weather, orbs, trainers) for m in moves]
        average = sum(r["damage"] for r in runs) / len(runs)
        if best is None or average > best[0]:
            best = (average, team, runs)
    _, team, runs = best
    search = {"candidates": len(candidates), "considered": len(considered), "parties": count,
              "approximate": len(candidates) > len(considered)}
    return _report(reference, foe, limit_s, weather, orbs, trainers, moves, list(team), runs, excluded, non_max, search)


def _summary(values):
    return {"avg": round(sum(values) / len(values), 1), "min": round(min(values), 1), "max": round(max(values), 1)}


def _report(reference, foe, limit_s, weather, orbs, trainers, moves, team, runs, excluded, non_max, search) -> dict:
    party = []
    for position, c in enumerate(team):
        f = c["fighter"]
        member = [r["members"][position] for r in runs]
        party.append({
            "order": position + 1, "id": f.key, **f.info, "name": f.name,
            "types": [TYPE_NAMES.get(t, t) for t in f.types], "fast": f.fast.name,
            "charged": f.charged[0].name if f.charged and c["charged"] else None,
            "max_attack": {"name": f.max_attack["name"], "level": f.max_attack["level"], "power": f.max_attack["power"],
                           "type": TYPE_NAMES.get(f.max_attack["type"])},
            "guard": f.guard, "spirit": f.spirit, "plan": PLAN_NAMES[c["plan"]],
            "damage": _summary([m["damage"] for m in member]),
            "max_damage": _summary([m["max_damage"] for m in member]),
            "alive_s": _summary([m["time_ms"] / 1000 for m in member]),
            "max_phases": _summary([m["max_phases"] for m in member]),
        })
    result = None
    if runs:
        result = {"damage": _summary([r["damage"] for r in runs]), "boss_hp": foe["hp"],
                  "defeated_scenarios": sum(1 for r in runs if r["defeated"]),
                  "battle_s": _summary([r["end_ms"] / 1000 for r in runs]),
                  "max_phases": _summary([r["max_phases"] for r in runs]),
                  "faints": _summary([r["faints"] for r in runs])}
    rule = foe["rule"]
    return {
        "engine": ENGINE_VERSION, "ruleset": RULESET["version"], "reference": reference.version,
        "boss": {"species": foe["species"], "form_name": foe["form_name"], "tier": foe["tier"],
                 "tier_name": foe["tier_name"], "hp": foe["hp"], "types": [TYPE_NAMES.get(t, t) for t in foe["types"]],
                 "attack": round(foe["attack"], 1), "defense": round(foe["defense"], 1)},
        "weather": weather, "weather_name": WEATHER_NAMES[weather], "time_limit_s": limit_s, "orbs": orbs, "trainers": trainers,
        "scenarios": [{"charged": m.name} for m in moves],
        "by_scenario": [{"charged": m.name, "damage": r["damage"], "defeated": r["defeated"],
                         "battle_s": r["end_ms"] / 1000, "max_phases": r["max_phases"]} for m, r in zip(moves, runs)],
        "party": party, "result": result, "excluded": excluded, "not_max_count": non_max, "search": search,
        "shortfall": (f"맥스배틀에 쓸 수 있는 개체가 {len(team)}마리라 3마리를 채우지 못했습니다." if len(team) < PARTY_SIZE else None),
        "assumptions": [
            ("혼자 싸운다고 가정합니다." if trainers == 1 else
             f"{trainers}명이 함께 싸우며, 한 마리 노림 공격은 {trainers}번 중 1번만 나에게 온다고 보고 피해를 1/{trainers}로 나눕니다.")
            + " 다른 트레이너의 피해·게이지·회복은 0으로 둡니다(보스가 더 빨리 쓰러지는 효과도 넣지 않음).",
            "필드에는 내 포켓몬 1마리만 있고, 기절하면 1초 뒤 다음 개체가 나옵니다. 3마리가 모두 기절하면 끝납니다.",
            f"맥스 게이지: 공격마다 max(1, floor(피해 × {rule['meter']} ÷ (보스 HP ÷ 200))) 충전"
            + (", 15초마다 에너지 구슬 +10을 모두 줍는다고 가정합니다." if orbs else ", 에너지 구슬은 줍지 않는다고 가정합니다."),
            "게이지가 차면 살아 있는 개체 중 맥스 기술이 가장 센 개체가 다이맥스해 3초 뒤 맥스 기술 3번을 씁니다. 그동안 보스는 공격하지 않습니다.",
            "맥스어택 250/300/350, 거다이맥스 기술 350/400/450, 다이월 20/40/60 보호막(최대 3개), 다이리커버리 최대 HP의 8/12/16% 회복. 박스에 맥스기술 정보가 없으면 맥스어택 레벨 1만 있다고 봅니다.",
            f"보스는 {rule['interval']:g}초마다 스페셜어택으로 공격하며, 한 마리 노림 공격(피하지 않으면 2배)과 전체 공격(1배)을 번갈아 씁니다. 회피하지 않습니다.",
            "보스가 몇 분 뒤 강해지는 격앙은 넣지 않았습니다. 날씨 보너스는 내 기술·맥스 기술·보스 기술에 같은 규칙으로 적용합니다.",
            f"보스 HP·배율·공격 간격·제한 시간은 커뮤니티 자료 기반 {RULESET['version']} 표의 값이며 보스마다 다를 수 있습니다.",
        ],
    }

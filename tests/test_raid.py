import itertools
import math
import unittest

from boxscan import raid
from boxscan.reference import BaseStats, Reference

TYPES = ["NORMAL", "FIGHTING", "FLYING", "POISON", "GROUND", "ROCK", "BUG", "GHOST", "STEEL",
         "FIRE", "WATER", "GRASS", "ELECTRIC", "PSYCHIC", "ICE", "DRAGON", "DARK", "FAIRY"]


def chart() -> dict:
    table = {a: {d: 1.0 for d in TYPES} for a in TYPES}
    table["WATER"]["FIRE"] = 1.6
    table["FIRE"]["WATER"] = 0.625
    table["GRASS"]["FIRE"] = 0.625
    return table


MOVES = {  # synthetic moves; timing fields like the game master's
    "SPLASH_FAST": {"type": "WATER", "power": 10.0, "energy": 10, "duration_ms": 1000, "damage_start_ms": 500},
    "WAVE": {"type": "WATER", "power": 100.0, "energy": -50, "duration_ms": 2000, "damage_start_ms": 1500},
    "LEAF_FAST": {"type": "GRASS", "power": 10.0, "energy": 10, "duration_ms": 1000, "damage_start_ms": 500},
    "BLADE": {"type": "GRASS", "power": 120.0, "energy": -100, "duration_ms": 2500, "damage_start_ms": 2000},
    "EMBER_FAST": {"type": "FIRE", "power": 5.0, "energy": 5, "duration_ms": 1000, "damage_start_ms": 500},
    "BLAST": {"type": "FIRE", "power": 100.0, "energy": -100, "duration_ms": 3000, "damage_start_ms": 2500},
    "PUNCH": {"type": "FIRE", "power": 50.0, "energy": -50, "duration_ms": 2000, "damage_start_ms": 1500},
}
BATTLE = {"sameTypeAttackBonusMultiplier": 1.2, "maximumEnergy": 100, "energyDeltaPerHealthLost": 0.5,
          "swapDurationMs": 1000, "enemyAttackInterval": 1.5, "bossEnergyRegenerationPerHealthLost": 0.5}


def reference() -> Reference:
    stats = {1: [BaseStats(1, "Flame", "Normal", 200, 150, 200)],
             2: [BaseStats(2, "Wave", "Normal", 180, 160, 180)],
             3: [BaseStats(3, "Leaf", "Normal", 170, 170, 170)]}
    learnsets = {"1|Normal": {"fast": ["EMBER_FAST"], "charged": ["BLAST", "PUNCH"]},
                 "2|Normal": {"fast": ["SPLASH_FAST"], "charged": ["WAVE"]},
                 "3|Normal": {"fast": ["LEAF_FAST"], "charged": ["BLADE"]}}
    names = {"SPLASH_FAST": "물튀기기", "WAVE": "파도", "LEAF_FAST": "잎", "BLADE": "잎날", "EMBER_FAST": "불씨",
             "BLAST": "폭발불", "PUNCH": "불펀치"}
    cpm = {1 + i / 2: round(0.1 + 0.0137 * i, 6) for i in range(0, 99)}
    return Reference("t", "", stats, cpm, {"불꽃몬": 1, "물몬": 2, "풀몬": 3}, move_names=names,
                     move_power={k: v["power"] for k, v in MOVES.items()}, learnsets=learnsets,
                     types={"1|Normal": ["FIRE"], "2|Normal": ["WATER"], "3|Normal": ["GRASS"]},
                     move_stats=MOVES, type_chart=chart(), weather={"CLEAR": ["FIRE", "GRASS"], "RAINY": ["WATER"]},
                     battle=BATTLE)


def entry(number, species, iv=(15, 15, 15), level=40.0, fast="SPLASH_FAST", charged=("WAVE",), **extra):
    return {"id": f"id{number}", "code": f"PK{number:04d}", "revision": 1, "species": species,
            "iv": dict(zip(("attack", "defense", "stamina"), iv)), "level": {"value": level},
            "moves": {"fast": {"move_id": fast}, "charged": [{"move_id": c} for c in charged]}, **extra}


class DamageTests(unittest.TestCase):
    def test_damage_formula_matches_a_hand_calculation(self):
        data = reference()
        move = raid._move(data, "WAVE")
        # 0.5 * 100 * 200 / 100 * STAB 1.2 * weather 1.2 * super effective 1.6 = 230.4 -> 230 + 1
        self.assertEqual(raid.damage(data, move, 200, 100, ["WATER"], ["FIRE"], ["WATER"]), 231)
        # without STAB, weather or effectiveness: 0.5 * 100 * 2 = 100 -> 101
        self.assertEqual(raid.damage(data, move, 200, 100, ["GRASS"], ["NORMAL"], []), 101)

    def test_boss_stats_use_tier_hp_cpm_and_iv_15(self):
        foe = raid.boss(reference(), "불꽃몬", "3")
        self.assertEqual(foe["hp"], 3600)
        self.assertAlmostEqual(foe["attack"], (200 + 15) * 0.73)
        self.assertAlmostEqual(foe["defense"], (150 + 15) * 0.73)
        self.assertEqual([m.move_id for m in foe["charged"]], ["BLAST", "PUNCH"])
        with self.assertRaises(raid.RaidError):
            raid.boss(reference(), "없는몬", "5")


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.data = reference()
        self.foe = raid.boss(self.data, "불꽃몬", "1")

    def fighter(self, number, species="물몬", **kwargs):
        fighter, reason = raid.attacker(self.data, entry(number, species, **kwargs))
        self.assertIsNone(reason)
        return fighter

    def test_hits_land_at_the_damage_window_and_time_limit_stops_the_battle(self):
        fighter = self.fighter(1)
        harmless = raid.Move("X_FAST", "x", "FIRE", 0.0, 0, 1000, 500)  # deals 1 damage per hit
        result = raid.simulate(self.data, [(fighter, 0)], self.foe, harmless, raid._move(self.data, "BLAST"), 30)
        fast = raid.damage(self.data, fighter.fast, fighter.attack, self.foe["defense"], ["WATER"], ["FIRE"], [])
        charged = raid.damage(self.data, fighter.charged[0], fighter.attack, self.foe["defense"], ["WATER"], ["FIRE"], [])
        # 30 s: cycles of 5 fast (5 s, 50 energy) + 1 charged (2 s); energy from the boss's 1-damage hits
        # adds a little, so the count is checked against a lower bound and the cap at boss HP.
        self.assertFalse(result["defeated"] and result["damage"] < self.foe["hp"])
        self.assertGreaterEqual(result["damage"], min(self.foe["hp"], 4 * (5 * fast + charged)))
        self.assertLessEqual(result["damage"], self.foe["hp"])
        self.assertEqual(result["members"][0]["faints"], 0)

    def test_boss_defeat_caps_damage_and_records_the_time(self):
        strong = self.fighter(1, level=50.0)
        foe = {**self.foe, "hp": 300}
        result = raid.simulate(self.data, [(strong, 0)], foe, raid._move(self.data, "EMBER_FAST"),
                               raid._move(self.data, "BLAST"), 180)
        self.assertTrue(result["defeated"])
        self.assertEqual(result["damage"], 300)
        self.assertLess(result["end_ms"], 180_000)

    def test_party_swaps_and_optional_rejoin(self):
        weak = [self.fighter(n, "풀몬", iv=(0, 0, 0), level=5.0, fast="LEAF_FAST", charged=("BLADE",)) for n in (1, 2)]
        party = [(f, 0) for f in weak]
        args = (self.foe, raid._move(self.data, "EMBER_FAST"), raid._move(self.data, "BLAST"), 180)
        once = raid.simulate(self.data, party, *args)
        self.assertEqual((once["faints"], once["rejoins"]), (2, 0))
        self.assertLess(once["end_ms"], 180_000)  # damage stops when everyone faints
        again = raid.simulate(self.data, party, *args, rejoin_delay_s=10)
        self.assertGreater(again["rejoins"], 0)
        self.assertGreater(again["damage"], once["damage"])


class ShadowAndWeatherTests(unittest.TestCase):
    def setUp(self):
        self.data = reference()
        self.foe = raid.boss(self.data, "불꽃몬", "5")

    def test_shadow_attacks_harder_and_takes_more_damage(self):
        plain, _ = raid.attacker(self.data, entry(1, "물몬"))
        shadow, _ = raid.attacker(self.data, entry(2, "물몬", shadow=True))
        self.assertAlmostEqual(shadow.attack, plain.attack * 1.2)
        self.assertAlmostEqual(shadow.defense, plain.defense * 0.8333333)
        self.assertTrue(shadow.name.endswith("(그림자)"))
        args = (self.foe, raid._move(self.data, "EMBER_FAST"), raid._move(self.data, "BLAST"), 300)
        normal_run = raid.simulate(self.data, [(plain, 0)], *args)
        shadow_run = raid.simulate(self.data, [(shadow, 0)], *args)
        normal_dps = normal_run["damage"] / normal_run["members"][0]["time_ms"]
        shadow_dps = shadow_run["damage"] / shadow_run["members"][0]["time_ms"]
        self.assertGreater(shadow_dps, normal_dps)
        self.assertLess(shadow_run["members"][0]["time_ms"], normal_run["members"][0]["time_ms"])

    def test_weather_boosts_both_sides_and_every_weather_is_compared(self):
        fighter, _ = raid.attacker(self.data, entry(1, "물몬"))
        ember = raid._move(self.data, "EMBER_FAST")
        sunny = raid.damage(self.data, ember, self.foe["attack"], fighter.defense, ["FIRE"], ["WATER"], ["FIRE"])
        plain = raid.damage(self.data, ember, self.foe["attack"], fighter.defense, ["FIRE"], ["WATER"], [])
        self.assertGreaterEqual(sunny, plain)  # the boss's fire attack is boosted in clear weather
        rows = raid.compare_weather(self.data, [entry(1, "물몬"), entry(2, "풀몬", fast="LEAF_FAST", charged=("BLADE",))],
                                    "불꽃몬", "5")
        self.assertEqual(len(rows), len(raid.WEATHER_NAMES))
        self.assertEqual(rows[0]["weather"], "RAINY")  # water attackers gain the most in rain
        self.assertEqual([r["damage"]["avg"] for r in rows], sorted((r["damage"]["avg"] for r in rows), reverse=True))


class RecommendTests(unittest.TestCase):
    def setUp(self):
        self.data = reference()

    def test_recommend_picks_super_effective_attackers_and_reports_exclusions(self):
        entries = [entry(1, "물몬"), entry(2, "풀몬", fast="LEAF_FAST", charged=("BLADE",)),
                   entry(3, "불꽃몬", fast="EMBER_FAST", charged=("BLAST", "PUNCH")),
                   {**entry(4, "물몬"), "iv": None}, {**entry(5, "물몬"), "level": {"value": None}}]
        result = raid.recommend(self.data, entries, "불꽃몬", "3")
        self.assertEqual(result["party"][0]["name"], "물몬")
        self.assertEqual([e["reason"] for e in result["excluded"]], ["IV 없음", "레벨이 하나로 정해지지 않음"])
        self.assertIn("6마리를 채우지 못했습니다", result["shortfall"])
        self.assertEqual(len(result["by_scenario"]), 2)  # one boss fast x two boss charged
        self.assertLessEqual(result["result"]["damage"]["min"], result["result"]["damage"]["avg"])
        flame = next(p for p in result["party"] if p["name"] == "불꽃몬")
        self.assertIsNotNone(flame["charged_note"])  # two charged attacks were compared

    def test_fixed_boss_moves_weather_and_mega_rule(self):
        entries = [entry(1, "물몬")]
        fixed = raid.recommend(self.data, entries, "불꽃몬", "5", boss_fast="EMBER_FAST", boss_charged="PUNCH")
        self.assertEqual(fixed["scenarios"], [{"fast": "불씨", "charged": "불펀치"}])
        self.assertFalse(fixed["result"]["defeated_scenarios"])  # 15,000 HP: damage is not capped
        rainy = raid.recommend(self.data, entries, "불꽃몬", "5", weather="RAINY", boss_fast="EMBER_FAST",
                               boss_charged="PUNCH")
        self.assertGreater(rainy["result"]["damage"]["avg"], fixed["result"]["damage"]["avg"])
        for bad in ({"include_mega": True}, {"weather": "STORM"}, {"time_limit_s": 5}, {"boss_charged": "WAVE"}):
            with self.subTest(bad=bad), self.assertRaises(raid.RaidError):
                raid.recommend(self.data, entries, "불꽃몬", "3", **bad)

    def test_search_matches_exhaustive_search_on_a_small_box(self):
        entries = [entry(1, "물몬"), entry(2, "물몬", iv=(5, 5, 5), level=30.0),
                   entry(3, "풀몬", fast="LEAF_FAST", charged=("BLADE",)),
                   entry(4, "불꽃몬", fast="EMBER_FAST", charged=("BLAST", "PUNCH"), level=35.0)]
        for rejoin in (None, 10):
            with self.subTest(rejoin=rejoin):
                result = raid.recommend(self.data, entries, "불꽃몬", "3", rejoin_delay_s=rejoin)
                foe = raid.boss(self.data, "불꽃몬", "3")
                scenarios = raid._scenarios(foe, None, None)
                fighters = [raid.attacker(self.data, e)[0] for e in entries]
                best = 0.0
                for size in range(1, len(fighters) + 1):
                    for team in itertools.permutations(fighters, size):
                        for choices in itertools.product(*[range(len(f.charged)) for f in team]):
                            party = list(zip(team, choices))
                            runs = [raid.simulate(self.data, party, foe, f, c, 180, "", rejoin) for f, c in scenarios]
                            best = max(best, sum(r["damage"] for r in runs) / len(runs))
                # The search keeps one charged attack per Pokémon and orders by damage per second, so it
                # may fall slightly short of the exhaustive best; it must stay within 5 %.
                self.assertGreaterEqual(result["result"]["damage"]["avg"], best * 0.95)
                self.assertLessEqual(result["result"]["damage"]["avg"], best + 1e-9)


class RaidApiTests(unittest.TestCase):
    def setUp(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from fastapi.testclient import TestClient
        from boxscan import reference as reference_module
        from boxscan.api import create_app
        from boxscan.box import BoxStore
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        self.client = TestClient(create_app(self.data_dir), base_url="http://localhost")
        self.addCleanup(self.client.close)
        self.missing = self.client.get("/api/raid/options")  # before any reference data exists
        folder = self.data_dir / "reference" / "t"
        folder.mkdir(parents=True)
        (folder / "reference.json").write_text(json.dumps(reference_module.to_json(reference())), encoding="utf-8")
        (self.data_dir / "reference" / "current").write_text("t", encoding="utf-8")
        box = BoxStore(self.data_dir)
        for number, species, fast, charged in ((1, "물몬", "SPLASH_FAST", "WAVE"), (2, "풀몬", "LEAF_FAST", "BLADE")):
            box.approve("scan", number, {"species": species, "iv": {"attack": 15, "defense": 15, "stamina": 15},
                                         "level": {"value": 40.0},
                                         "moves": {"fast": {"move_id": fast}, "charged": [{"move_id": charged}]}},
                        {"scan_id": "scan", "individual": number})

    def test_options_and_recommend(self):
        self.assertEqual(self.missing.status_code, 409)
        options = self.client.get("/api/raid/options", params={"species": "불꽃몬"}).json()
        self.assertIn("불꽃몬", options["species"])
        self.assertEqual([m["name"] for m in options["boss"]["charged"]], ["폭발불", "불펀치"])
        result = self.client.post("/api/raid/recommend", json={"species": "불꽃몬", "tier": "3", "weather": "RAINY"})
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual([p["name"] for p in body["party"]], ["물몬", "풀몬"])
        self.assertEqual(body["party"][0]["code"], "PK0001")
        self.assertEqual(body["weather_name"], "비")
        compared = self.client.post("/api/raid/recommend", json={"species": "불꽃몬", "tier": "3",
                                                                 "compare_weather": True}).json()
        self.assertEqual(len(compared["weather_comparison"]), 8)
        self.assertEqual(self.client.post("/api/raid/recommend", json={"species": ""}).status_code, 422)
        self.assertEqual(self.client.post("/api/raid/recommend", json={"species": "불꽃몬", "include_mega": True}).status_code, 422)
        self.assertEqual(self.client.get("/raid").status_code, 200)

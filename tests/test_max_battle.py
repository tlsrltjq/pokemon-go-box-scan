import itertools
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import max_battle, raid
from test_raid import entry, reference as raid_reference

MAX_MOVES = {
    "VN_BM_009": {"type": "WATER", "kind": "attack", "name": "다이스트림", "vfx": "max_geyser"},
    "VN_BM_017": {"type": "GRASS", "kind": "attack", "name": "다이그래스", "vfx": "max_overgrowth"},
    "VN_BM_046": {"type": "WATER", "kind": "gmax", "name": None, "vfx": "gmax_cannonade"},
    "VN_BM_052": {"type": "NORMAL", "kind": "guard", "name": "다이월", "vfx": "max_shield"},
    "VN_BM_053": {"type": "NORMAL", "kind": "spirit", "name": "다이리커버리", "vfx": "max_heal"},
}


def reference():
    data = raid_reference()
    data.max_moves = MAX_MOVES
    data.max_move_by_type = {"WATER": "VN_BM_009", "GRASS": "VN_BM_017"}
    data.species_max_moves = {"2|*": "VN_BM_046"}
    return data


def max_entry(number, species="물몬", state="dynamax", **extra):
    return entry(number, species, max_state=state, **extra)


class FighterTests(unittest.TestCase):
    def setUp(self):
        self.data = reference()

    def test_max_attack_follows_the_fast_move_and_defaults_to_level_1(self):
        f, reason = max_battle.fighter(self.data, max_entry(1))
        self.assertIsNone(reason)
        self.assertEqual(f.max_attack, {"type": "WATER", "power": 250, "name": "다이스트림", "level": 1, "gmax": False})
        self.assertEqual((f.guard, f.spirit), (None, None))
        leaf, _ = max_battle.fighter(self.data, max_entry(2, "풀몬", fast="LEAF_FAST", charged=("BLADE",)))
        self.assertEqual((leaf.max_attack["name"], leaf.max_attack["type"]), ("다이그래스", "GRASS"))

    def test_box_max_moves_set_levels_and_unlock_guard_and_spirit(self):
        moves = [{"name": "다이스트림", "level": 3}, {"name": "다이월", "level": 2},
                 {"name": "다이리커버리", "level": 1, "locked": True}]
        f, _ = max_battle.fighter(self.data, max_entry(1, max_moves=moves))
        self.assertEqual((f.max_attack["level"], f.max_attack["power"], f.guard, f.spirit), (3, 350, 2, None))

    def test_gigantamax_uses_the_species_move(self):
        f, _ = max_battle.fighter(self.data, max_entry(1, state="gigantamax",
                                                       max_moves=[{"name": "거다이포격", "level": 2}]))
        self.assertEqual(f.max_attack, {"type": "WATER", "power": 400, "name": "거다이포격", "level": 2, "gmax": True})
        unnamed, _ = max_battle.fighter(self.data, max_entry(2, state="gigantamax"))
        self.assertEqual((unnamed.max_attack["name"], unnamed.max_attack["power"]), ("거다이맥스 기술 (물)", 350))

    def test_ineligible_entries_say_why(self):
        self.assertEqual(max_battle.fighter(self.data, entry(1, "물몬"))[1], "다이맥스·거다이맥스 개체가 아님")
        self.assertEqual(max_battle.fighter(self.data, max_entry(1, level=None))[1], "레벨이 하나로 정해지지 않음")
        self.assertEqual(max_battle.fighter(self.data, max_entry(1, fast="NOPE"))[1], "노말어택을 기술 사전과 연결하지 못함")


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.data = reference()
        self.foe = max_battle.boss(self.data, "불꽃몬", "1")
        self.water, _ = max_battle.fighter(self.data, max_entry(1))

    def run_party(self, party, **kwargs):
        options = {"time_limit_s": 480, **kwargs}
        return max_battle.simulate(self.data, party, self.foe, self.foe["charged"][0], **options)

    def test_meter_fills_from_hits_and_the_max_phase_finishes_the_boss(self):
        # Boss defense (150 + 15) x 0.15 = 24.75; attack (180 + 15) x CPM(40) = 227.877.
        # Splash: 0.5 x 10 x 227.877 / 24.75 x STAB 1.2 x 1.6 = 88.4 -> 89 per hit; meter
        # floor(89 / (1700 / 200)) = 10 per hit, so 10 hits (890 damage) fill it by 9.5 s. The Max Phase
        # starts at 10 s; after the 3 s countdown the first Max Attack (2209 damage) lands at 15 s.
        result = self.run_party([(self.water, False, "attack")], orbs=False)
        self.assertTrue(result["defeated"])
        self.assertEqual(result["end_ms"], 15000)
        self.assertEqual(result["max_phases"], 1)
        self.assertEqual(result["damage"], 1700)
        self.assertEqual(result["members"][0]["max_damage"], 1700 - 890)

    def test_orbs_trainers_and_guard_change_the_outcome(self):
        tough = max_battle.boss(self.data, "불꽃몬", "3")
        weak, _ = max_battle.fighter(self.data, max_entry(1, fast="LEAF_FAST", charged=("BLADE",),
                                                         species="풀몬", max_moves=[{"name": "다이월", "level": 3}]))

        def run(plan="attack", **kwargs):
            return max_battle.simulate(self.data, [(weak, False, plan)], tough, tough["charged"][0], 600, **kwargs)

        alone, shared = run(orbs=False), run(orbs=False, trainers=4)
        self.assertGreater(shared["end_ms"], alone["end_ms"])  # single-target attacks are split
        self.assertEqual((run(trainers=4)["max_phases"], run(orbs=False, trainers=4)["max_phases"]), (1, 0))
        self.assertGreater(run("guard", trainers=4)["end_ms"], run("attack", trainers=4)["end_ms"])  # shield

    def test_spirit_heals_and_faints_bring_in_the_next_member(self):
        foe = max_battle.boss(self.data, "불꽃몬", "4")
        spirit, _ = max_battle.fighter(self.data, max_entry(2, max_moves=[{"name": "다이리커버리", "level": 3}]))
        result = max_battle.simulate(self.data, [(self.water, False, "attack"), (spirit, False, "spirit")], foe,
                                     foe["charged"][0], 480)
        self.assertEqual(result["faints"], 2)
        self.assertGreater(result["members"][1]["time_ms"], 0)


class RecommendTests(unittest.TestCase):
    def setUp(self):
        self.data = reference()
        self.entries = [
            max_entry(1), max_entry(2, level=20.0), max_entry(3, level=30.0, state="gigantamax"),
            max_entry(4, "풀몬", fast="LEAF_FAST", charged=("BLADE",)),
            max_entry(5, "불꽃몬", fast="EMBER_FAST", charged=("BLAST",)),
            entry(6, "물몬"),  # not a Max Pokémon
            max_entry(7, level=None),
        ]

    def test_recommend_picks_three_and_reports_exclusions(self):
        result = max_battle.recommend(self.data, self.entries, "불꽃몬", "3", weather="RAINY")
        self.assertEqual(len(result["party"]), 3)
        self.assertEqual(result["party"][0]["code"], "PK0001")  # super effective and level 40 leads
        self.assertNotIn("PK0004", [p["code"] for p in result["party"]])  # grass is resisted
        self.assertEqual(result["result"]["defeated_scenarios"], 2)
        self.assertEqual(result["excluded"], [{"code": "PK0007", "name": "물몬", "reason": "레벨이 하나로 정해지지 않음"}])
        self.assertEqual(result["not_max_count"], 1)
        self.assertEqual(result["weather_name"], "비")
        self.assertEqual(len(result["by_scenario"]), 2)
        self.assertIsNone(result["shortfall"])

    def test_search_matches_exhaustive_search(self):
        result = max_battle.recommend(self.data, self.entries, "불꽃몬", "4", boss_charged="BLAST")
        foe = max_battle.boss(self.data, "불꽃몬", "4")
        fighters = [max_battle.fighter(self.data, e)[0] for e in self.entries]
        fighters = [f for f in fighters if f]
        best = 0
        for team in itertools.permutations(fighters, 3):
            for charged in itertools.product([False, True], repeat=3):
                party = [(f, c, "attack") for f, c in zip(team, charged)]
                run = max_battle.simulate(self.data, party, foe, foe["charged"][0], 480)
                best = max(best, run["damage"])
        # The engine fixes each member's charged-attack choice from its solo run, so it may fall short
        # of the full search, but not by much on this box.
        self.assertGreaterEqual(result["result"]["damage"]["avg"], best * 0.9)
        self.assertLessEqual(result["result"]["damage"]["avg"], best)

    def test_shortfall_empty_box_and_errors(self):
        result = max_battle.recommend(self.data, self.entries[:1], "불꽃몬", "1")
        self.assertIn("1마리", result["shortfall"])
        empty = max_battle.recommend(self.data, [entry(1, "물몬")], "불꽃몬", "1")
        self.assertIsNone(empty["result"])
        for kwargs in ({"tier": "9"}, {"trainers": 5}, {"weather": "X"}, {"boss_charged": "WAVE"},
                       {"time_limit_s": 10}):
            with self.assertRaises(raid.RaidError):
                max_battle.recommend(self.data, self.entries, "불꽃몬", **{"tier": "1", **kwargs})


class MaxApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from boxscan import reference as reference_module
        from boxscan.api import create_app
        from boxscan.box import BoxStore
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        data_dir = Path(temporary.name)
        self.client = TestClient(create_app(data_dir), base_url="http://localhost")
        self.addCleanup(self.client.close)
        folder = data_dir / "reference" / "t"
        folder.mkdir(parents=True)
        (folder / "reference.json").write_text(json.dumps(reference_module.to_json(reference())), encoding="utf-8")
        (data_dir / "reference" / "current").write_text("t", encoding="utf-8")
        box = BoxStore(data_dir)
        box.approve("scan", 1, {"species": "물몬", "iv": {"attack": 15, "defense": 15, "stamina": 15},
                                "level": {"value": 40.0}, "max_state": "dynamax",
                                "max_moves": [{"name": "다이스트림", "level": 2}],
                                "moves": {"fast": {"move_id": "SPLASH_FAST"}, "charged": [{"move_id": "WAVE"}]}},
                    {"scan_id": "scan", "individual": 1})

    def test_options_and_recommend(self):
        options = self.client.get("/api/max/options", params={"species": "불꽃몬"}).json()
        self.assertEqual([t["id"] for t in options["tiers"]], ["1", "2", "3", "4", "5", "gmax"])
        self.assertEqual([m["name"] for m in options["boss"]["charged"]], ["폭발불", "불펀치"])
        response = self.client.post("/api/max/recommend", json={"species": "불꽃몬", "tier": "2", "trainers": "4"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["party"][0]["max_attack"]["level"], 2)
        self.assertEqual(body["trainers"], 4)
        self.assertEqual(self.client.post("/api/max/recommend", json={"species": "불꽃몬", "trainers": 2.5}).status_code, 422)
        self.assertEqual(self.client.post("/api/max/recommend", json={"species": ""}).status_code, 422)
        self.assertEqual(self.client.get("/max").status_code, 200)


if __name__ == "__main__":
    unittest.main()

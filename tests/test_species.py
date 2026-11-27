import unittest

from boxscan import species
from boxscan.moves import annotate
from boxscan.reference import BaseStats, Reference

from test_reference_level import synthetic_reference


def named_reference() -> Reference:
    stats = {646: [BaseStats(646, "Kyurem", "Normal", 246, 170, 245)],
             6: [BaseStats(6, "Charizard", "Normal", 223, 173, 186)],
             383: [BaseStats(383, "Groudon", "Normal", 270, 228, 205)]}
    temp = {6: {"MEGA_X": BaseStats(6, "Charizard", "Mega_X", 273, 213, 186),
                "MEGA_Y": BaseStats(6, "Charizard", "Mega_Y", 319, 212, 186)},
            383: {"PRIMAL": BaseStats(383, "Groudon", "Primal", 353, 268, 218)}}
    changes = {646: [{"target": "Black", "type": "FUSE", "component": "제크로무", "stats": [310, 183, 245],
                      "existing_moves": [], "replacement_moves": []}]}
    names = {"큐레무": 646, "리자몽": 6, "그란돈": 383}
    return Reference("t", "", stats, {1.0: 0.1}, names, temp_stats=temp, form_changes=changes)


class SpeciesNameTests(unittest.TestCase):
    def test_screen_names_split_into_species_and_state(self):
        data = named_reference()
        cases = {
            "리자몽": ("리자몽", None, None),
            "메가리자몽X": ("리자몽", "MEGA_X", None),
            "원시그란돈": ("그란돈", "PRIMAL", None),
            "블랙큐레무": ("큐레무", None, "Black"),
            "메가없는몬": ("메가없는몬", None, None),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                found = species.resolve(name, data)
                self.assertEqual((found["species"], found["temp_evolution"], found["form"]), expected)
                self.assertEqual(found["screen_name"], name)

    def test_timer_only_mega_and_fusion_options(self):
        data = named_reference()
        self.assertEqual(species.single_mega(data, "그란돈"), "PRIMAL")
        self.assertIsNone(species.single_mega(data, "리자몽"))  # X or Y: cannot tell
        self.assertEqual(species.fusion_options(data, "큐레무"), [
            {"type": "FUSE", "component": "제크로무", "target": "Black", "target_name": "블랙큐레무"}])

    def test_mega_eligibility_and_whether_it_mega_evolved_before(self):
        data = synthetic_reference()
        self.assertEqual(species.mega_kinds(data, "테스트몬5"), ["MEGA"])
        self.assertEqual(species.mega_kinds(data, "테스트몬40"), [])
        self.assertIsNone(species.mega_info(data, {"species": "테스트몬40"}))
        self.assertIsNone(species.mega_info(data, {"species": "테스트몬5", "shadow": True}))  # shadows cannot
        self.assertIsNone(species.mega_info(None, {"species": "테스트몬5"}))
        fresh = species.mega_info(data, {"species": "테스트몬5"})
        self.assertEqual((fresh["kinds"], fresh["evolved"], fresh["plus_moves"], fresh["plus_confirmed"], fresh["plus_off"]),
                         (["메가진화"], False, ["기술12번+"], False, False))
        for proof in ({"temp_observation": {"cp": 1}}, {"mega_level": 1}, {"mega_move": {"move_id": "X"}},
                      {"mega_evolved": True}):
            with self.subTest(proof=proof):
                self.assertTrue(species.mega_evolved(proof))
        self.assertFalse(species.mega_evolved({"mega_level": 2, "mega_evolved": False}))  # the user's choice wins
        self.assertFalse(species.mega_evolved({"mega_level": 0}))
        seen = species.mega_info(data, {"species": "테스트몬5", "mega_level": 3, "mega_move_off": True,
                                        "mega_move": {"move_id": "VM_MOVE_TEMP_EVOLUTION_MEGA_V0005_POKEMON_MON5"}})
        self.assertEqual((seen["evolved"], seen["level"], seen["plus_confirmed"], seen["plus_off"]), (True, 3, True, True))

    def test_mega_plus_move_matches_species_data_or_stays_as_read(self):
        data = synthetic_reference()
        moves = {"fast": None, "charged": [], "mega_plus": {"value": "기술12번", "votes": 1, "observations": 1, "power": 140}}
        annotate(moves, data, "테스트몬5")
        plus = moves["mega_plus"]
        self.assertEqual((plus["name"], plus["temp_evolution"], plus["reference_power"]), ("기술12번+", "MEGA", 140.0))
        self.assertIsNone(plus["mega_level"])  # no "+N" bonus read
        boosted = {"fast": None, "charged": [], "mega_plus": {"value": "기술12번", "votes": 1, "observations": 1,
                                                              "power": 140, "boost": 14}}
        annotate(boosted, data, "테스트몬5")
        self.assertEqual(boosted["mega_plus"]["mega_level"], 1)  # 140 x (1.1 - 1) = 14: the level with x1.1
        from boxscan.moves import mega_level_from_boost
        self.assertIsNone(mega_level_from_boost(data, 140, 7))  # no level gives +7
        unknown = {"fast": None, "charged": [], "mega_plus": {"value": "새기술", "votes": 1, "observations": 1, "power": 140}}
        annotate(unknown, data, "테스트몬5")
        self.assertEqual((unknown["mega_plus"]["name"], unknown["mega_plus"]["match"]), ("새기술+", "none"))
        self.assertIn("아직 없는", unknown["mega_plus"]["reason"])

    def test_shadow_only_move_marks_a_shadow_candidate(self):
        from boxscan.analysis import apply_reference
        data = synthetic_reference()
        individual = {"species": {"value": "테스트몬2", "votes": 1, "observations": 1},
                      "cp": None, "max_hp": None, "appraisals": [], "purify_button": {"value": None, "votes": 0},
                      "moves": {"fast": None, "charged": [{"value": "기술15번", "votes": 1, "observations": 1, "power": 15}]}}
        apply_reference(individual, data)
        self.assertEqual(individual["shadow"], {"value": True, "evidence": ["그림자 전용 기술"]})
        individual["moves"]["charged"] = []
        individual["purify_button"] = {"value": True, "votes": 2}
        apply_reference(individual, data)
        self.assertEqual(individual["shadow"]["evidence"], ["정화 버튼"])

    def test_fusion_replacement_move_gets_a_fusion_reason(self):
        data = synthetic_reference()
        moves = {"fast": None, "charged": [{"value": "기술20번", "votes": 1, "observations": 1, "power": 20}]}
        annotate(moves, data, "테스트몬6", "Fused")
        (move,) = moves["charged"]
        self.assertEqual((move["marker"], move["marker_kind"]), ("*", "form_change"))
        self.assertEqual(move["reason"], "테스트몬7과 합체해야 쓸 수 있는 기술 (분리하면 다른 기술로 바뀜)")

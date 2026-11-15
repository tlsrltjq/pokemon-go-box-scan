import unittest

from boxscan.moves import annotate

from test_reference_level import synthetic_reference


def voted(name, power=None, votes=3):
    return {"value": name, "votes": votes, "observations": 3, "power": power, "weather_boost": None}


class MoveDictionaryTests(unittest.TestCase):
    def setUp(self):
        self.data = synthetic_reference()

    def test_regular_elite_and_shadow_moves(self):
        moves = {"fast": voted("기술1번", 1), "charged": [voted("기술13번", 13), voted("기술15번", 15)]}
        annotate(moves, self.data, "테스트몬2")
        self.assertEqual(moves["dictionary"], "species")
        self.assertEqual((moves["fast"]["move_id"], moves["fast"]["marker"]), ("MOVE_1_FAST", ""))
        elite, shadow = moves["charged"]
        self.assertEqual((elite["move_id"], elite["marker"]), ("MOVE_13", "*"))
        self.assertIn("엘리트", elite["reason"])
        self.assertEqual(shadow["marker"], "*")
        self.assertIn("그림자", shadow["reason"])

    def test_close_ocr_text_is_corrected_within_the_learnset(self):
        moves = {"fast": None, "charged": [voted("기술12빈", 12)]}
        annotate(moves, self.data, "테스트몬2")
        (move,) = moves["charged"]
        self.assertEqual((move["name"], move["match"], move["ocr_text"]), ("기술12번", "corrected", "기술12빈"))

    def test_known_move_outside_learnset_and_unknown_text(self):
        moves = {"fast": voted("기술50번", 50), "charged": [voted("전혀없는기술")]}
        annotate(moves, self.data, "테스트몬2")
        self.assertEqual(moves["fast"]["marker"], "*")
        self.assertIn("목록에 없는", moves["fast"]["reason"])
        self.assertEqual((moves["charged"][0]["match"], moves["charged"][0]["marker"]), ("none", ""))

    def test_without_species_or_reference(self):
        moves = {"fast": voted("기술13번", 13), "charged": []}
        annotate(moves, self.data, None)
        self.assertEqual(moves["dictionary"], "all")
        self.assertEqual((moves["fast"]["move_id"], moves["fast"]["marker"]), ("MOVE_13", ""))
        untouched = {"fast": voted("기술1번"), "charged": []}
        annotate(untouched, None, "테스트몬2")
        self.assertEqual(untouched["dictionary"], "unavailable")
        self.assertNotIn("marker", untouched["fast"])

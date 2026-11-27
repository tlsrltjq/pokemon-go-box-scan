import unittest
from datetime import datetime

from boxscan import ai_export

from test_reference_level import synthetic_reference

ENTRY = {
    "id": "a", "code": "02", "revision": 1, "species": "테스트몬5", "shiny": True, "cp": 1500, "max_hp": 120,
    "iv": {"attack": 15, "defense": 14, "stamina": 13}, "level": {"value": 30.0, "status": "RESOLVED_BY_CONSTRAINTS"},
    "moves": {"fast": {"name": "기술1번"}, "charged": [{"name": "기술|13번"}]}, "mega_level": 2,
    "max_state": "dynamax", "max_moves": [{"name": "다이스트림", "level": 2}],
}
UNKNOWN = {"id": "b", "code": "01", "revision": 1, "species": "테스트몬40",
           "level": {"value": None, "candidates": [20.0, 20.5], "status": "AMBIGUOUS"}, "moves": {}}


class AiExportTests(unittest.TestCase):
    def setUp(self):
        self.data = synthetic_reference()

    def text(self, raids=None, entries=(ENTRY, UNKNOWN), candies=None):
        return ai_export.markdown(list(entries), self.data, candies, "1.0", raids, "raid-sim/4, max-sim/2",
                                  now=datetime(2026, 11, 27, 12, 0))

    def test_box_table_keeps_unknowns_and_escapes_cells(self):
        text = self.text()
        self.assertIn("만든 시각: 2026-11-27 12:00", text)
        self.assertIn("계산 엔진: raid-sim/4, max-sim/2", text)
        self.assertIn("숫자는 다시 계산하지 마세요", text)
        self.assertIn("## 내 박스 (2마리)", text)
        row = next(line for line in text.splitlines() if line.startswith("| 02 |"))
        self.assertIn("테스트몬5*", row)
        self.assertIn("15/14/13 | 93%", row)
        self.assertIn("기술\\|13번", row)  # a pipe cannot break the table
        self.assertIn("다이맥스(다이스트림 Lv2)", row)
        self.assertIn("메가 Lv.2(+기술 기술12번+)", row)
        unknown = next(line for line in text.splitlines() if line.startswith("| 01 |"))
        self.assertIn("후보 20, 20.5", unknown)
        self.assertIn("| - |", unknown)  # unknown values are "-", never 0
        self.assertLess(text.index("테스트몬40"), text.index("테스트몬5*"))  # sorted by species
        self.assertNotIn("현재 레이드", text.split("## 계산 기준")[0])  # no battle section unless asked
        for private in ("filename", "frames", ".mp4", "scan_id"):
            self.assertNotIn(private, text)

    def test_resources_and_battles(self):
        candies = {"candies": {"테스트몬5": {"candy": 120, "xl": 3, "energy": 200, "energy_kind": "메가"}},
                   "stardust": {"value": 345678}}
        boss = {"kind": "raid", "label": "그란돈", "tier_name": "5성 (전설)", "types": ["땅"]}
        summary = {
            "party": [{"name": "테스트몬5", "code": "02", "level": 30.0, "fast": "기술1번", "charged": "기술13번"}],
            "clear_s": {"avg": 251.24}, "damage": {"avg": 15000}, "boss_hp": 15000, "scenarios": 4,
            "defeated_scenarios": 3, "dodge_comparison": {"chosen": "tank"},
            "mega_comparison": {"options": {
                "off": {"member": None, "clear_s": 251.24, "damage": 15000},
                "evolved": {"member": "메가테스트몬5", "code": "02", "clear_s": 230.0, "damage": 15000},
                "all": {"member": "메가테스트몬5", "code": "02", "clear_s": 230.0, "damage": 15000}}},
        }
        max_boss = {"kind": "max", "label": "물몬", "tier_name": "3성", "types": ["물"]}
        max_summary = {"party": [{"name": "테스트몬5", "code": "02", "level": 30.0, "fast": "기술1번", "charged": None,
                                  "max_move": "다이스트림"}],
                       "clear_s": None, "damage": {"avg": 5000}, "boss_hp": 10000, "scenarios": 2, "defeated_scenarios": 0}
        text = self.text([(boss, summary), (max_boss, max_summary)], candies=candies)
        self.assertIn("별의모래: 345,678", text)
        self.assertIn("| 테스트몬5 | 120 | 3 | 200 (메가) |", text)
        self.assertIn("### 그란돈 · 레이드 5성 (전설) · 땅", text)
        self.assertIn("- 추천: 테스트몬5(02번, Lv30.0) 기술1번/기술13번", text)
        self.assertIn("평균 251.2초에 처치 · 보스 기술 조합 4가지 중 3가지에서 처치", text)
        self.assertIn("다 맞으면서 하는 쪽", text)
        self.assertIn("메가진화해 본 개체 중 메가테스트몬5(02번)을 넣으면 평균 230.0초에 처치 (메가 없이 평균 251.2초)", text)
        self.assertEqual(text.count("메가테스트몬5(02번)"), 1)  # the same "all" result is not repeated
        self.assertIn("### 물몬 · 맥스배틀 3성 · 물", text)
        self.assertIn("기술1번/스페셜어택 안 씀, 다이스트림", text)
        self.assertIn("처치 못 함, 평균 피해 5,000 (보스 HP 10,000의 50%)", text)
        empty = self.text([])
        self.assertIn("저장된 현재 보스 목록이 없습니다", empty)

    def test_without_game_data_the_file_still_works(self):
        text = ai_export.markdown([ENTRY], None, None, "1.0")
        self.assertIn("게임 데이터 버전: 없음", text)
        self.assertIn("별의모래: 기록 없음", text)
        self.assertNotIn("메가 Lv", text)  # mega eligibility needs the game data
        self.assertTrue(ai_export.filename().endswith(".md"))


if __name__ == "__main__":
    unittest.main()

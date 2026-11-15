import io
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from boxscan import export
from boxscan.api import create_app
from boxscan.box import BoxStore

from test_reference_level import synthetic_reference

FULL = {
    "species": "하나몬", "dex": 1, "form": "Normal", "cp": 1500, "max_hp": 120,
    "shiny": True, "lucky": False, "background": True, "max_state": "gigantamax",
    "max_moves": [{"name": "거다이감쇠", "level": 1, "locked": False}, {"name": "다이월", "level": None, "locked": True}],
    "fusion": [{"type": "FUSE", "component": "두나몬", "target": "Fused", "target_name": "합체몬"}],
    "cp_source": "calculated", "temp_observation": {"temp_evolution": "MEGA", "cp": 2100},
    "mega_move": {"name": "기술99번+", "move_id": None, "reason": "메가진화 중에만"},
    "iv": {"attack": 15, "defense": 12, "stamina": 15},
    "level": {"value": 30.5, "status": "RESOLVED_BY_CONSTRAINTS", "candidates": [30.5]},
    "moves": {"fast": {"name": "기술1번", "move_id": "MOVE_1_FAST", "marker": "", "reason": None},
              "charged": [{"name": "기술13번", "move_id": "MOVE_13", "marker": "*", "reason": "레거시"},
                          {"name": "기술11번", "move_id": "MOVE_11", "marker": "", "reason": None}]},
}
PARTIAL = {"species": "=HYPERLINK(\"http://evil.test\")", "cp": 300,
           "level": {"value": None, "status": "AMBIGUOUS", "candidates": [10.0, 10.5]}}


class ExportTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.box = BoxStore(self.root)
        self.box.approve("scan", 0, FULL, {"filename": "private-video.mp4"})
        self.box.approve("scan", 1, PARTIAL, {"filename": "private-video.mp4"})

    def workbook(self, reference=None):
        data = export.workbook_bytes(self.box.list(), reference, "9.9.9")
        return load_workbook(io.BytesIO(data)), data

    def test_pokemon_sheet_values_and_empty_unknowns(self):
        book, _ = self.workbook()
        self.assertEqual(book.sheetnames, ["포켓몬 목록", "기술", "메타데이터"])
        sheet = book["포켓몬 목록"]
        headers = [cell.value for cell in sheet[1]]
        row = dict(zip(headers, [cell.value for cell in sheet[2]]))
        self.assertEqual((row["관리 번호"], row["종"], row["CP"], row["레벨"]), ("PK0001", "하나몬*◆", 1500, 30.5))
        for removed in ("이름 표시", "이로치", "반짝반짝", "배경"):
            self.assertNotIn(removed, headers)
        self.assertEqual((row["IV 공격"], row["IV 방어"], row["IV HP"]), (15, 12, 15))
        self.assertAlmostEqual(row["IV %"], 42 / 45)
        self.assertEqual((row["스페셜어택 1"], row["스페셜어택 2"]), ("기술13번*", "기술11번"))
        self.assertIsNone(row["모습"])  # the regular form stays empty
        self.assertEqual((row["다이맥스"], row["맥스기술"], row["합체·모습 변화"]),
                         ("거다이맥스", "거다이감쇠 레벨 1, 다이월 잠김", "두나몬 → 합체몬"))
        self.assertEqual((row["CP 출처"], row["메가진화 중 CP"]), ("계산값", 2100))
        self.assertIsInstance(row["마지막 갱신"], datetime)
        partial = dict(zip(headers, [cell.value for cell in sheet[3]]))
        self.assertIsNone(partial["IV 공격"])  # unknown stays empty, never 0
        self.assertIsNone(partial["최대 HP"])
        self.assertEqual((partial["레벨 판정"], partial["레벨 후보"]), ("후보 여러 개", "10, 10.5"))

    def test_text_that_looks_like_a_formula_stays_text(self):
        book, raw = self.workbook()
        cell = book["포켓몬 목록"]["B3"]
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(cell.value.startswith("=HYPERLINK"))
        self.assertNotIn(b"private-video", raw)

    def test_moves_and_metadata_sheets(self):
        book, _ = self.workbook(synthetic_reference())
        moves = [[cell.value for cell in row] for row in book["기술"].iter_rows(min_row=2)]
        self.assertEqual(moves[0][2], "노말어택")
        self.assertEqual(moves[1], ["PK0001", "하나몬*◆", "스페셜어택 1", "기술13번", "MOVE_13", "*", "레거시"])
        self.assertEqual(len(moves), 6)
        self.assertEqual(moves[3][2:4], ["메가진화 추가 스페셜어택", "기술99번+"])
        self.assertEqual(moves[4][2:4] + [moves[4][6]], ["맥스기술", "거다이감쇠", "레벨 1"])
        meta = {row[0].value: row[1].value for row in book["메타데이터"].iter_rows(min_row=2)}
        self.assertEqual((meta["개체 수"], meta["앱 버전"], meta["기준 데이터 버전"]), (2, "9.9.9", "test"))
        self.assertEqual(book["메타데이터"]["B3"].number_format, "General")

    def test_api_download(self):
        client = TestClient(create_app(self.root), base_url="http://localhost")
        self.addCleanup(client.close)
        response = client.get("/api/exports/box.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], export.MEDIA_TYPE)
        self.assertIn("attachment; filename=\"pokemon-box-", response.headers["content-disposition"])
        self.assertEqual(load_workbook(io.BytesIO(response.content))["포켓몬 목록"].max_row, 3)

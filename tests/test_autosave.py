import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fastapi.testclient import TestClient

from boxscan import autosave
from boxscan.api import create_app
from boxscan.box import BoxStore, Conflict
from boxscan.store import JobStore

from test_accuracy import IV, candidate

OTHER_IV = {"attack": 1, "defense": 2, "stamina": 3}


class AutoSaveTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = JobStore(Path(temporary.name) / "data")
        self.box = BoxStore(self.store.root)

    def scan(self, scan_id, individuals):
        self.store.create(scan_id, "합성.mp4")
        self.store.claim()
        self.store.finish(scan_id)
        self.store.set_analysis(scan_id, "done")
        folder = self.store.root / scan_id
        folder.mkdir(exist_ok=True)
        (folder / "analysis.json").write_text(json.dumps({
            "individuals": individuals, "frames": [], "unassigned_appraisals": [],
            "recognizer": {"ocr": "fake"}, "created_at": "2026-01-01T00:00:00+00:00"}, ensure_ascii=False),
            encoding="utf-8")

    def saved_box(self):
        """둘몬 01 and 셋몬 01 saved from an earlier scan."""
        self.scan("s0", [candidate([1], "둘몬", 500, 80), candidate([2], "셋몬", 600, 90)])
        return autosave.run(self.store, self.box, "s0", None)["saved"]

    def test_passing_candidates_are_saved_and_doubtful_ones_held(self):
        first = self.saved_box()
        self.assertEqual([item["action"] for item in first], ["new", "new"])
        self.scan("s1", [
            candidate([1], "하나몬", 500, 80),                         # 0 new
            candidate([2], "하나몬", 349, 81, status="CONFLICT"),      # 1 level does not fit
            candidate([3], "둘몬", 650, 80, code="01"),                # 2 update of 둘몬 01 (powered up)
            candidate([4], "셋몬", 700, 90),                           # 3 no number, 셋몬 with this IV exists
            candidate([5], "넷몬", 800, 95), candidate([6], "넷몬", 810, 95),  # 4, 5 one Pokémon split in two
            candidate([7], "다섯몬", 900, 99, code="01"),              # 6 number without an entry
            candidate([8], "둘몬", 400, 79, code="01"),                # 7 lower CP than the entry
            candidate([9], "셋몬", 700, 90, iv=OTHER_IV),              # 8 another 셋몬: new
        ])
        result = autosave.run(self.store, self.box, "s1", None)
        self.assertEqual(result["rule"], autosave.RULE_VERSION)
        self.assertEqual({item["index"]: item["action"] for item in result["saved"]}, {0: "new", 2: "update", 8: "new"})
        reasons = {item["index"]: " / ".join(item["reasons"]) for item in result["check"]}
        self.assertEqual(sorted(reasons), [1, 3, 4, 5, 6, 7])
        self.assertIn("레벨 CONFLICT", reasons[1])
        self.assertIn("박스에 같은 종·IV 개체가 있음(01번)", reasons[3])
        self.assertIn("한 마리가 둘로 나뉘었을 수 있음", reasons[4])
        self.assertIn("관리 번호 01에 맞는 다섯몬가 박스에 없음", reasons[6])
        self.assertIn("CP가 낮아졌습니다", reasons[7])

        updated = self.box.get(first[0]["entry"]["id"])
        self.assertEqual((updated["cp"], updated["revision"], updated["code"]), (650, 2, "01"))
        last = updated["revisions"][-1]
        self.assertEqual((last["source"]["kind"], last["source"]["rule"]), ("auto_save", autosave.RULE_VERSION))
        approval = self.box.approval("s1", 2)
        self.assertEqual((approval["auto"], approval["change"], approval["undoable"]), (True, "updated", True))
        self.assertFalse(self.box.approval("s0", 0)["undoable"])  # updated by a later scan

        again = autosave.run(self.store, self.box, "s1", None)  # nothing saved twice
        self.assertEqual((again["saved"], len(again["check"])), ([], 6))

    def test_a_save_can_be_undone_while_the_entry_is_unchanged(self):
        first = self.saved_box()
        self.scan("s1", [candidate([1], "하나몬", 500, 80), candidate([2], "둘몬", 650, 80, code="01")])
        saved = autosave.run(self.store, self.box, "s1", None)["saved"]
        self.assertEqual(self.box.undo_approval("s1", 0)["result"], "removed")
        self.assertNotIn(saved[0]["entry"]["id"], [e["id"] for e in self.box.list()])
        self.assertEqual(self.box.by_code("하나몬", "01")["deleted"], True)  # the number stays reserved
        self.assertEqual(self.box.undo_approval("s1", 1)["result"], "restored")
        self.assertEqual(self.box.get(first[0]["entry"]["id"])["cp"], 500)
        self.assertIsNone(self.box.approval("s1", 1))
        items, _ = autosave.plan("s1", json.loads((self.store.root / "s1" / "analysis.json").read_text("utf-8"))["individuals"],
                                 self.box, None)
        self.assertEqual(items[1]["status"], "ready")  # can be saved again
        with self.assertRaises(Conflict):
            self.box.undo_approval("s1", 1)  # nothing to undo any more
        # An entry changed after the save is not rolled back.
        redone = autosave.run(self.store, self.box, "s1", None)["saved"][-1]["entry"]
        entry = self.box.get(redone["id"])
        self.box.edit(entry["id"], {**entry, "nickname": "둘몬01"}, entry["revision"])
        with self.assertRaises(Conflict):
            self.box.undo_approval("s1", 1)

    def test_setting_is_off_by_default_and_only_then_saves_after_analysis(self):
        self.assertFalse(autosave.enabled(self.store.root))
        with mock.patch.object(autosave, "run") as run:
            autosave.after_analysis(self.store, "s1")
            run.assert_not_called()
            autosave.set_enabled(self.store.root, True)
            self.assertTrue(autosave.enabled(self.store.root))
            with mock.patch("boxscan.reference.load", return_value=None):
                autosave.after_analysis(self.store, "s1")  # without game data nothing would pass
            run.assert_not_called()
            with mock.patch("boxscan.reference.load", return_value=object()),                     mock.patch("sys.stderr", new_callable=io.StringIO) as log:
                run.side_effect = RuntimeError("boom")
                autosave.after_analysis(self.store, "s1")  # never breaks the analysis, only logs
            self.assertIn("RuntimeError: boom", log.getvalue())
            run.assert_called_once()
        (self.store.root / autosave.SETTINGS).write_text("[", encoding="utf-8")
        self.assertFalse(autosave.enabled(self.store.root))  # a broken file means off

    def test_raw_values_match_the_review_form(self):
        individual = candidate([1], "하나몬", 500, 80, charged=("B", "C"),
                               nickname={"value": "하나몬01"}, state={"temp_evolution": "MEGA"})
        individual["moves"]["mega_plus"] = {"value": "D", "name": "D+", "mega_level": 2}
        raw = autosave.raw_values(individual)
        self.assertEqual((raw["species"], raw["cp"], raw["iv"], raw["fast_move"], raw["charged_moves"]),
                         ("하나몬", 500, IV, "A", ["B", "C"]))
        self.assertEqual((raw["temp_evolution"], raw["mega_move"], raw["mega_level"], raw["nickname"]),
                         ("MEGA", "D+", 2, "하나몬01"))


class AutoSaveApiTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = JobStore(Path(temporary.name) / "data")
        self.client = TestClient(create_app(self.store.root), base_url="http://localhost")
        self.addCleanup(self.client.close)
        self.scan_id = "22222222-2222-4222-8222-222222222222"
        individuals = [candidate([1], "하나몬", 500, 80), candidate([2], "하나몬", 349, 81, status="CONFLICT")]
        for individual in individuals:  # the fields the scan page summary reads
            individual.update(start_seconds=0, end_seconds=2)
            individual["appraisals"] = [{"iv": IV, "stable_seconds": 1, "start_seconds": 0}]
        AutoSaveTests.scan(self, self.scan_id, individuals)

    def test_scan_shows_the_rules_and_settings_and_undo_work(self):
        scan = self.client.get(f"/api/scans/{self.scan_id}").json()
        self.assertEqual(scan["auto_save"], {"enabled": False, "rule": autosave.RULE_VERSION, "reference": False})
        first, second = scan["analysis"]["individuals"]
        self.assertEqual((first["auto"]["status"], first["auto"]["action"]), ("ready", "new"))
        self.assertEqual(second["auto"]["status"], "check")
        self.assertIn("레벨 CONFLICT", second["auto"]["reasons"])

        self.assertEqual(self.client.get("/api/settings").json(), {"auto_save": False})
        self.assertEqual(self.client.put("/api/settings", json={"auto_save": True}).json(), {"auto_save": True})
        self.assertEqual(self.client.put("/api/settings", json={"auto_save": "yes"}).status_code, 422)
        self.assertEqual(self.client.put("/api/settings", json={"auto_save": True},
                                         headers={"Origin": "http://evil.test"}).status_code, 403)
        # Without game data the level cannot be checked, so the button refuses.
        self.assertEqual(self.client.post(f"/api/scans/{self.scan_id}/auto-save").status_code, 409)

        saved = self.client.post(f"/api/scans/{self.scan_id}/individuals/0/approve",
                                 json={"values": {"species": "하나몬", "cp": 500}}).json()
        approval = self.client.get(f"/api/scans/{self.scan_id}").json()["analysis"]["individuals"][0]["approval"]
        self.assertEqual((approval["auto"], approval["undoable"]), (False, True))
        undo = self.client.delete(f"/api/scans/{self.scan_id}/individuals/0/approval")
        self.assertEqual(undo.json(), {"pokemon_id": saved["id"], "result": "removed"})
        self.assertEqual(self.client.delete(f"/api/scans/{self.scan_id}/individuals/0/approval").status_code, 409)
        self.assertIsNone(self.client.get(f"/api/scans/{self.scan_id}").json()["analysis"]["individuals"][0]["approval"])


if __name__ == "__main__":
    unittest.main()

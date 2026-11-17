import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from boxscan import review
from boxscan.api import create_app
from boxscan.box import BoxStore, Conflict, merge
from boxscan.store import JobStore

from test_reference_level import CPM, IV, synthetic_reference

SOURCE = {"scan_id": "s", "individual": 0}


class BoxStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.box = BoxStore(self.root)

    def test_new_entry_gets_code_and_retry_returns_the_same_entry(self):
        first = self.box.approve("scan", 0, {"species": "하나몬", "cp": 100}, SOURCE)
        self.assertEqual((first["code"], first["revision"], first["duplicate"]), ("PK0001", 1, False))
        again = self.box.approve("scan", 0, {"species": "하나몬", "cp": 999}, SOURCE)
        self.assertEqual((again["id"], again["cp"], again["duplicate"]), (first["id"], 100, True))
        self.assertEqual(len(self.box.list()), 1)
        self.assertEqual(self.box.approval("scan", 0)["code"], "PK0001")
        self.assertIsNone(self.box.approval("scan", 1))

    def test_update_keeps_identity_and_does_not_erase_unread_values(self):
        first = self.box.approve("scan", 0, {"species": "하나몬", "cp": 100, "max_hp": 50,
                                             "moves": {"fast": {"name": "A"}, "charged": [{"name": "B"}]}},
                                 SOURCE)
        updated = self.box.approve("rescan", 0, {"species": None, "cp": 150, "max_hp": None,
                                                 "moves": {"fast": None, "charged": [{"name": "C"}]}},
                                   SOURCE, target=first["id"], expected_revision=1)
        self.assertEqual((updated["id"], updated["code"], updated["revision"]), (first["id"], "PK0001", 2))
        self.assertEqual((updated["species"], updated["cp"], updated["max_hp"]), ("하나몬", 150, 50))
        self.assertEqual(updated["moves"], {"fast": {"name": "A"}, "charged": [{"name": "C"}]})
        detail = self.box.get(first["id"])
        self.assertEqual([r["change"] for r in detail["revisions"]], ["updated", "created"])
        self.assertEqual(detail["revisions"][0]["before"]["cp"], 100)

    def test_stale_revision_and_missing_target_are_conflicts(self):
        first = self.box.approve("scan", 0, {"cp": 100}, SOURCE)
        self.box.approve("scan", 1, {"cp": 120}, SOURCE, target=first["id"], expected_revision=1)
        with self.assertRaises(Conflict):
            self.box.approve("scan", 2, {"cp": 130}, SOURCE, target=first["id"], expected_revision=1)
        with self.assertRaises(Conflict):
            self.box.approve("scan", 3, {"cp": 130}, SOURCE, target="missing")
        self.assertIsNone(self.box.approval("scan", 2))

    def test_delete_is_soft_and_codes_are_never_reused(self):
        first = self.box.approve("scan", 0, {"cp": 100}, SOURCE)
        with self.assertRaises(Conflict):
            self.box.delete(first["id"], expected_revision=5)
        self.box.delete(first["id"], expected_revision=1)
        self.assertEqual(self.box.list(), [])
        self.assertIsNone(self.box.get(first["id"]))
        self.assertTrue(self.box.approval("scan", 0)["deleted"])
        second = self.box.approve("scan", 1, {"cp": 200}, SOURCE)
        self.assertEqual(second["code"], "PK0002")

    def test_list_filters_by_species_and_store_opens_concurrently(self):
        self.box.approve("scan", 0, {"species": "하나몬"}, SOURCE)
        self.box.approve("scan", 1, {"species": "두나몬"}, SOURCE)
        self.assertEqual([e["species"] for e in self.box.list("두나몬")], ["두나몬"])
        errors = []
        threads = [threading.Thread(target=lambda: errors.extend(
            [e] if (e := self._open()) else [])) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def _open(self):
        try:
            BoxStore(self.root / "fresh")
        except Exception as error:
            return error
        return None

    def test_by_code_finds_current_and_removed_entries(self):
        first = self.box.approve("scan", 0, {"species": "하나몬"}, {"scan_id": "scan", "individual": 0})
        self.assertEqual(self.box.by_code("PK0001")["id"], first["id"])
        self.box.delete(first["id"], first["revision"])
        self.assertTrue(self.box.by_code("PK0001")["deleted"])
        for code in ("PK0002", "pk0001", "PK1", None):
            self.assertIsNone(self.box.by_code(code))

    def test_merge_ignores_empty_values(self):
        self.assertEqual(merge({"cp": 1, "iv": {"attack": 1}}, {"cp": None, "iv": {}}),
                         {"cp": 1, "iv": {"attack": 1}})
        # An explicit "no" from the user replaces an earlier "yes"; unknown keeps it.
        self.assertEqual(merge({"shiny": True, "lucky": True}, {"shiny": False, "lucky": None}),
                         {"shiny": False, "lucky": True})


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.data = synthetic_reference()
        self.base = self.data.forms("테스트몬40")[0]

    def test_normalize_recomputes_level_and_marks_moves(self):
        from boxscan import level
        cp = level.cp(self.base, IV, CPM[30.0])
        hp = level.hp(self.base, IV, CPM[30.0])
        values = review.normalize({
            "species": "테스트몬40", "cp": str(cp), "max_hp": hp,
            "iv": {k: str(v) for k, v in IV.items()},
            "fast_move": "기술1번", "charged_moves": ["기술13번", ""],
        }, self.data)
        self.assertEqual((values["dex"], values["cp"], values["iv"]), (40, cp, IV))
        self.assertEqual(values["level"]["value"], 30.0)
        self.assertEqual(values["moves"]["fast"]["marker"], "")
        self.assertEqual([m["marker"] for m in values["moves"]["charged"]], ["*"])

    def test_mega_candidate_keeps_screen_cp_apart_and_stores_regular_cp(self):
        from boxscan import level
        mega = self.data.temp_stats[5]["MEGA"]
        regular = self.data.forms("테스트몬5")[0]
        multiplier = CPM[22.5]
        raw = {"species": "메가테스트몬5", "cp": level.cp(mega, IV, multiplier),
               "max_hp": level.hp(mega, IV, multiplier), "iv": IV}
        values = review.normalize(raw, self.data)
        self.assertEqual((values["species"], values["cp_source"]), ("테스트몬5", "calculated"))
        self.assertEqual(values["cp"], level.cp(regular, IV, multiplier))
        self.assertEqual(values["temp_observation"]["cp"], raw["cp"])
        with_plus = review.normalize({**raw, "mega_move": "기술12번+"}, self.data)
        self.assertEqual((with_plus["mega_move"]["name"], with_plus["moves"]["charged"]), ("기술12번+", []))
        unresolved = review.normalize({**raw, "cp": 11}, self.data)
        self.assertIsNone(unresolved["cp"])  # never store a mega CP as the regular CP
        self.assertEqual(unresolved["temp_observation"]["cp"], 11)

    def test_max_state_and_moves_are_validated(self):
        values = review.normalize({"cp": 100, "max_state": "gigantamax",
                                   "max_moves": [{"name": "거다이감쇠", "level": 1, "locked": False}]}, self.data)
        self.assertEqual((values["max_state"], values["max_moves"][0]["level"]), ("gigantamax", 1))
        for bad in ({"cp": 100, "max_state": "huge"}, {"cp": 100, "temp_evolution": "MEGA_Z"},
                    {"cp": 100, "max_moves": [{"name": "다이월", "level": 9}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                review.normalize(bad, self.data)

    def test_flags_are_strict_booleans(self):
        values = review.normalize({"cp": 100, "shiny": True, "lucky": False, "shadow": True}, self.data)
        self.assertEqual((values["shiny"], values["lucky"], values["background"], values["shadow"]),
                         (True, False, None, True))

    def test_normalize_rejects_bad_input(self):
        for raw in ({"cp": 5}, {"iv": {"attack": 1}}, {"iv": {"attack": 16, "defense": 1, "stamina": 1}},
                    {"charged_moves": ["a", "b", "c"]}, {}, {"cp": True}, "text",
                    {"cp": 100, "shiny": "yes"}, {"cp": 100, "background": 1}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                review.normalize(raw, self.data)

    def test_without_reference_values_are_kept_as_typed(self):
        values = review.normalize({"species": "아무몬", "fast_move": "몸통박치기"}, None)
        self.assertEqual(values["level"]["status"], "NO_REFERENCE")
        self.assertEqual(values["moves"]["fast"]["name"], "몸통박치기")

    def test_warnings_for_a_possibly_different_individual(self):
        old = {"species": "하나몬", "iv": {"attack": 1, "defense": 2, "stamina": 3}, "cp": 500}
        self.assertEqual(review.warnings(old, {"species": "하나몬", "iv": old["iv"], "cp": 600}), [])
        found = review.warnings(old, {"species": "두나몬", "iv": {"attack": 1, "defense": 2, "stamina": 4},
                                      "cp": 400})
        self.assertEqual(len(found), 3)


class ApprovalApiTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = JobStore(Path(temporary.name) / "data")
        self.client = TestClient(create_app(self.store.root), base_url="http://localhost")
        self.addCleanup(self.client.close)
        self.scan_id = "11111111-1111-4111-8111-111111111111"
        self.store.create(self.scan_id, "합성.mp4")
        self.store.claim()
        self.store.finish(self.scan_id)
        folder = self.store.root / self.scan_id
        folder.mkdir()
        individual = {"frames": [1, 2], "start_seconds": 0, "end_seconds": 2,
                      "cp": {"value": 100, "votes": 1, "observations": 1}, "max_hp": None,
                      "species": {"value": "하나몬", "votes": 1, "observations": 1}}
        (folder / "analysis.json").write_text(json.dumps({
            "individuals": [individual, dict(individual)], "frames": [], "unassigned_appraisals": [],
            "recognizer": {"ocr": "fake"}, "created_at": "2026-01-01T00:00:00+00:00",
        }), encoding="utf-8")

    def approve(self, index, body, **headers):
        return self.client.post(f"/api/scans/{self.scan_id}/individuals/{index}/approve",
                                json=body, headers=headers)

    def test_approve_new_then_update_with_warning_acknowledgement(self):
        values = {"species": "하나몬", "cp": 100, "iv": {"attack": 1, "defense": 2, "stamina": 3}}
        created = self.approve(0, {"values": values})
        self.assertEqual(created.status_code, 200, created.text)
        entry = created.json()
        self.assertEqual(entry["code"], "PK0001")
        self.assertEqual(entry["level"]["status"], "NO_REFERENCE")  # no reference data in this test
        scan = self.client.get(f"/api/scans/{self.scan_id}").json()
        self.assertEqual(scan["analysis"]["individuals"][0]["approval"]["code"], "PK0001")
        self.assertIsNone(scan["analysis"]["individuals"][1]["approval"])

        changed = {**values, "cp": 150, "iv": {"attack": 1, "defense": 2, "stamina": 4}}
        body = {"values": changed, "target": entry["id"], "expected_revision": 1}
        warned = self.approve(1, body)
        self.assertEqual(warned.status_code, 409)
        self.assertIn("IV가 다릅니다", warned.json()["warnings"][0])
        updated = self.approve(1, {**body, "acknowledge": True}).json()
        self.assertEqual((updated["id"], updated["revision"], updated["cp"]), (entry["id"], 2, 150))
        detail = self.client.get(f"/api/pokemon/{entry['id']}").json()
        self.assertEqual(detail["revisions"][0]["source"]["filename"], "합성.mp4")

    def test_code_in_nickname_suggests_the_entry_and_warns_on_mismatch(self):
        entry = self.approve(0, {"values": {"species": "하나몬", "cp": 100, "nickname": "하나PK0001"}}).json()
        self.assertEqual(entry["nickname"], "하나PK0001")
        base = {"frames": [1], "start_seconds": 0, "end_seconds": 1, "cp": None, "max_hp": None}

        def candidate(species, code):
            return {**base, "species": {"value": species, "votes": 1, "observations": 1},
                    "code": {"value": code, "votes": 1, "observations": 1} if code else None}

        path = self.store.root / self.scan_id / "analysis.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["individuals"] = [result["individuals"][0], candidate("하나몬", "PK0001"),
                                 candidate("두나몬", "PK0001"), candidate("하나몬", "PK0009")]
        path.write_text(json.dumps(result), encoding="utf-8")
        found = [i["code_match"] for i in self.client.get(f"/api/scans/{self.scan_id}").json()["analysis"]["individuals"]]
        self.assertIsNone(found[0])
        self.assertEqual((found[1]["status"], found[1]["pokemon_id"]), ("match", entry["id"]))
        self.assertEqual((found[2]["status"], found[2]["species"]), ("species_conflict", "하나몬"))
        self.assertEqual(found[3], {"code": "PK0009", "status": "not_found"})

        other = self.approve(3, {"values": {"species": "하나몬", "cp": 100}}).json()  # PK0002
        warned = self.approve(1, {"values": {"species": "하나몬", "cp": 100}, "target": other["id"],
                                  "expected_revision": 1})
        self.assertEqual(warned.status_code, 409)
        self.assertIn("관리번호는 PK0001", warned.json()["warnings"][0])
        matched = self.approve(1, {"values": {"species": "하나몬", "cp": 120}, "target": entry["id"],
                                   "expected_revision": 1})
        self.assertEqual(matched.status_code, 200, matched.text)
        self.assertEqual(matched.json()["nickname"], "하나PK0001")  # blank nickname keeps the saved one
        self.client.delete(f"/api/pokemon/{entry['id']}", params={"revision": 2})
        found = self.client.get(f"/api/scans/{self.scan_id}").json()["analysis"]["individuals"][2]["code_match"]
        self.assertEqual(found["status"], "deleted")

    def test_rejects_invalid_requests(self):
        self.assertEqual(self.approve(5, {"values": {"cp": 100}}).status_code, 404)
        self.assertEqual(self.approve(0, {"values": {"cp": 1}}).status_code, 422)
        self.assertEqual(self.client.post(
            f"/api/scans/{self.scan_id}/individuals/0/approve", content=b"values=1",
            headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code, 415)
        self.assertEqual(self.approve(0, {"values": {"cp": 100}}, Origin="http://evil.test").status_code, 403)
        self.assertEqual(self.approve(0, {"values": {"cp": 100}, "target": "nope"}).status_code, 422)

    def test_list_and_delete(self):
        entry = self.approve(0, {"values": {"species": "하나몬", "cp": 100}}).json()
        self.assertEqual(len(self.client.get("/api/pokemon").json()["pokemon"]), 1)
        self.assertEqual(self.client.get("/api/pokemon", params={"species": "두나몬"}).json()["pokemon"], [])
        cross = self.client.delete(f"/api/pokemon/{entry['id']}", params={"revision": 1},
                                   headers={"Origin": "http://evil.test"})
        self.assertEqual(cross.status_code, 403)
        self.assertEqual(self.client.delete(f"/api/pokemon/{entry['id']}", params={"revision": 9}).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/pokemon/{entry['id']}", params={"revision": 1}).status_code, 204)
        self.assertEqual(self.client.get(f"/api/pokemon/{entry['id']}").status_code, 404)
        self.assertEqual(self.client.get("/box").status_code, 200)

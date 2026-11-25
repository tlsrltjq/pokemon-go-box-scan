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
        self.assertEqual((first["code"], first["revision"], first["duplicate"]), ("01", 1, False))
        again = self.box.approve("scan", 0, {"species": "하나몬", "cp": 999}, SOURCE)
        self.assertEqual((again["id"], again["cp"], again["duplicate"]), (first["id"], 100, True))
        self.assertEqual(len(self.box.list()), 1)
        self.assertEqual(self.box.approval("scan", 0)["code"], "01")
        self.assertIsNone(self.box.approval("scan", 1))

    def test_update_keeps_identity_and_does_not_erase_unread_values(self):
        first = self.box.approve("scan", 0, {"species": "하나몬", "cp": 100, "max_hp": 50,
                                             "moves": {"fast": {"name": "A"}, "charged": [{"name": "B"}]}},
                                 SOURCE)
        updated = self.box.approve("rescan", 0, {"species": None, "cp": 150, "max_hp": None,
                                                 "moves": {"fast": None, "charged": [{"name": "C"}]}},
                                   SOURCE, target=first["id"], expected_revision=1)
        self.assertEqual((updated["id"], updated["code"], updated["revision"]), (first["id"], "01", 2))
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
        self.assertEqual(second["code"], "02")  # same (unknown) species: the removed PK1 stays reserved

    def test_codes_count_within_each_species(self):
        def add(n, species):
            return self.box.approve("scan", n, {"species": species, "cp": 100}, SOURCE)
        first, other, second = add(0, "마기라스"), add(1, "망나뇽"), add(2, "마기라스")
        self.assertEqual([first["code"], other["code"], second["code"]], ["01", "01", "02"])
        # A corrected species takes that species' next number; the old one is never handed out again.
        moved = self.box.edit(second["id"], {**second, "species": "망나뇽"}, second["revision"])
        self.assertEqual(moved["code"], "02")
        self.assertEqual(add(3, "마기라스")["code"], "03")
        back = self.box.edit(moved["id"], {**moved, "species": "마기라스"}, moved["revision"])
        self.assertEqual(back["code"], "04")
        # An update that only fills in the species of an unknown entry moves it the same way.
        unknown = self.box.approve("scan", 4, {"cp": 100}, SOURCE)
        named = self.box.approve("scan", 5, {"species": "망나뇽"}, SOURCE, target=unknown["id"])
        self.assertEqual((unknown["code"], named["code"]), ("01", "03"))

    def test_user_can_choose_a_number_and_swap_with_its_holder(self):
        from boxscan.box import CodeTaken

        def add(n):
            return self.box.approve("scan", n, {"species": "그란돈", "cp": 100 + n}, SOURCE)
        a, b, c = add(0), add(1), add(2)  # 01, 02, 03
        moved = self.box.set_code(c["id"], 7, c["revision"])
        self.assertEqual((moved["code"], moved["revision"]), ("07", 2))
        with self.assertRaises(CodeTaken) as taken:
            self.box.set_code(moved["id"], 1, moved["revision"])
        self.assertEqual(taken.exception.holder["id"], a["id"])
        swapped = self.box.set_code(moved["id"], 1, moved["revision"], swap=True)
        self.assertEqual(swapped["code"], "01")
        self.assertEqual(self.box.get(a["id"])["code"], "07")  # the holder took the old number
        self.assertEqual(self.box.by_code("그란돈", "07")["id"], a["id"])
        # The freed 03 stays reserved for new entries but can be chosen on purpose.
        self.assertEqual(add(3)["code"], "08")
        back = self.box.set_code(b["id"], 3, self.box.get(b["id"])["revision"])
        self.assertEqual(back["code"], "03")
        history = self.box.get(a["id"])["revisions"][0]
        self.assertEqual((history["source"]["kind"], history["source"]["from"], history["source"]["to"], history["source"]["swapped"]),
                         ("code_change", "01", "07", True))
        with self.assertRaises(Conflict):
            self.box.set_code(a["id"], 9, 1)  # stale revision
        for bad in (0, 1000, "2", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.box.set_code(a["id"], bad, self.box.get(a["id"])["revision"])

    def test_candy_keeps_unread_values_and_can_be_corrected(self):
        self.box.record_resources({"family": "애버라스", "candy": 98, "xl": 12, "stardust": 1_180_103}, {"scan_id": "a"})
        self.box.record_resources({"family": "애버라스", "candy": 120}, {"scan_id": "b"})  # XL not read this time
        found = self.box.candies()
        self.assertEqual((found["candies"]["애버라스"]["candy"], found["candies"]["애버라스"]["xl"]), (120, 12))
        self.box.record_resources({"family": "애버라스", "candy": 120, "xl": 0, "xl_hidden": True}, {"scan_id": "c"})
        self.assertEqual(self.box.candies()["candies"]["애버라스"]["xl"], 0)  # all XL used: the hidden count replaces 12
        self.assertEqual(found["stardust"]["value"], 1_180_103)
        fixed = self.box.set_candy("애버라스", {"candy": "130", "xl": "", "energy": None})
        self.assertEqual((fixed["candy"], fixed["xl"]), (130, None))
        for bad in ({"candy": -1}, {"candy": "많음"}, {"energy_kind": "기타"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.box.set_candy("애버라스", bad)

    def test_box_from_before_per_species_codes_is_numbered_on_open(self):
        import sqlite3
        for n, species in enumerate(("마기라스", "망나뇽", "마기라스")):
            self.box.approve("scan", n, {"species": species}, SOURCE)
        with sqlite3.connect(self.root / "box.sqlite3") as db:
            db.execute("DROP TABLE codes")  # like a box (or backup) made before this version
        reopened = BoxStore(self.root)
        self.assertEqual([(e["species"], e["code"]) for e in reopened.list()],
                         [("마기라스", "01"), ("망나뇽", "01"), ("마기라스", "02")])

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
        for value in ("01", "1", "001", "PK1", "pk0001"):  # the earlier PK form still names number 1
            self.assertEqual(self.box.by_code("하나몬", value)["id"], first["id"], value)
        self.box.delete(first["id"], first["revision"])
        self.assertTrue(self.box.by_code("하나몬", "01")["deleted"])
        for species, code in (("하나몬", "02"), ("두나몬", "01"), ("하나몬", "0"), ("하나몬", "A1"),
                              (None, "01"), ("하나몬", None)):
            self.assertIsNone(self.box.by_code(species, code))

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
        self.assertEqual(entry["code"], "01")
        self.assertEqual(entry["level"]["status"], "NO_REFERENCE")  # no reference data in this test
        scan = self.client.get(f"/api/scans/{self.scan_id}").json()
        self.assertEqual(scan["analysis"]["individuals"][0]["approval"]["code"], "01")
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
        entry = self.approve(0, {"values": {"species": "하나몬", "cp": 100, "nickname": "하나몬01"}}).json()
        self.assertEqual((entry["nickname"], entry["code"]), ("하나몬01", "01"))
        base = {"frames": [1], "start_seconds": 0, "end_seconds": 1, "cp": None, "max_hp": None}

        def candidate(species, code):
            return {**base, "species": {"value": species, "votes": 1, "observations": 1},
                    "code": {"value": code, "votes": 1, "observations": 1} if code else None}

        path = self.store.root / self.scan_id / "analysis.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["individuals"] = [result["individuals"][0], candidate("하나몬", "01"),
                                 candidate("두나몬", "01"), candidate("하나몬", "09"), candidate(None, "01")]
        path.write_text(json.dumps(result), encoding="utf-8")
        found = [i["code_match"] for i in self.client.get(f"/api/scans/{self.scan_id}").json()["analysis"]["individuals"]]
        self.assertIsNone(found[0])
        self.assertEqual((found[1]["status"], found[1]["pokemon_id"]), ("match", entry["id"]))
        # The number counts within the species read on screen: 두나몬 has no PK1.
        self.assertEqual(found[2], {"code": "01", "status": "not_found", "species": "두나몬"})
        self.assertEqual(found[3], {"code": "09", "status": "not_found", "species": "하나몬"})
        self.assertEqual(found[4], {"code": "01", "status": "species_unknown"})

        other = self.approve(3, {"values": {"species": "하나몬", "cp": 100}}).json()  # 하나몬 PK2
        warned = self.approve(1, {"values": {"species": "하나몬", "cp": 100}, "target": other["id"],
                                  "expected_revision": 1})
        self.assertEqual(warned.status_code, 409)
        self.assertIn("관리번호는 01인데 하나몬 02", warned.json()["warnings"][0])
        matched = self.approve(1, {"values": {"species": "하나몬", "cp": 120}, "target": entry["id"],
                                   "expected_revision": 1})
        self.assertEqual(matched.status_code, 200, matched.text)
        self.assertEqual(matched.json()["nickname"], "하나몬01")  # blank nickname keeps the saved one
        self.client.delete(f"/api/pokemon/{entry['id']}", params={"revision": 2})
        found = self.client.get(f"/api/scans/{self.scan_id}").json()["analysis"]["individuals"][1]["code_match"]
        self.assertEqual(found["status"], "deleted")

    def test_code_change_api_asks_before_swapping(self):
        first = self.approve(0, {"values": {"species": "하나몬", "cp": 100}}).json()
        second = self.approve(1, {"values": {"species": "하나몬", "cp": 120}}).json()
        url = f"/api/pokemon/{second['id']}/code"
        taken = self.client.post(url, json={"number": 1, "expected_revision": second["revision"]})
        self.assertEqual(taken.status_code, 409)
        self.assertEqual((taken.json()["holder"]["id"], taken.json()["holder"]["code"]), (first["id"], "01"))
        done = self.client.post(url, json={"number": "1", "expected_revision": second["revision"], "swap": True})
        self.assertEqual((done.status_code, done.json()["code"]), (200, "01"))
        self.assertEqual(self.client.get(f"/api/pokemon/{first['id']}").json()["code"], "02")
        self.assertEqual(self.client.post(url, json={"number": 0, "expected_revision": done.json()["revision"]}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"number": 5}).status_code, 422)

    def test_saving_records_the_candy_read_on_screen(self):
        path = self.store.root / self.scan_id / "analysis.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["individuals"][0]["resources"] = {"family": "하나몬", "candy": 33, "xl": 6, "stardust": 1_189_489}
        path.write_text(json.dumps(result), encoding="utf-8")
        self.assertEqual(self.approve(0, {"values": {"species": "하나몬", "cp": 100}}).status_code, 200)
        candies = self.client.get("/api/candies").json()
        self.assertEqual((candies["candies"]["하나몬"]["candy"], candies["stardust"]["value"]), (33, 1_189_489))
        edited = self.client.patch("/api/candies/하나몬", json={"candy": 40, "xl": 6})
        self.assertEqual((edited.status_code, edited.json()["candy"]), (200, 40))
        self.assertEqual(self.client.patch("/api/candies/하나몬", json={"candy": "x"}).status_code, 422)
        self.assertIn("candy_family", self.client.get("/api/pokemon").json()["pokemon"][0])

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

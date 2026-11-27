"""Current raid boss list: download cache, mapping, recommendation cache, API and page.

Only synthetic boss lists and a synthetic reference are used; nothing goes to the internet.
"""
import json
import math
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import raid_bosses
from boxscan.box import BoxStore
from boxscan.reference import BaseStats
from test_max_battle import reference as max_reference

CPM_20 = 0.5974


def catch_cp(attack, defense, stamina):
    return math.floor((attack + 15) * math.sqrt(defense + 15) * math.sqrt(stamina + 15) * CPM_20 ** 2 / 10)


def reference():
    """The raid test reference with game-master-style names, a regional form, a mega and CPM 20."""
    data = max_reference()
    data.stats = {1: [BaseStats(1, "Flame", "Normal", 200, 150, 200), BaseStats(1, "Flame", "Alola", 210, 140, 200)],
                  2: [BaseStats(2, "Wave", "Normal", 180, 160, 180)],
                  3: [BaseStats(3, "Mr_Leaf", "Normal", 170, 170, 170)],
                  4: [BaseStats(4, "Nameless", "Normal", 100, 100, 100)]}
    data.cpm = {**data.cpm, 20.0: CPM_20}
    data.learnsets = {**data.learnsets, "1|Alola": data.learnsets["1|Normal"]}
    data.types = {**data.types, "1|Alola": ["FIRE", "GRASS"], "2|MEGA": ["WATER", "DRAGON"]}
    data.temp_stats = {2: {"MEGA": BaseStats(2, "Wave", "Mega", 260, 200, 180)}}
    data.temp_boosts = {"same_type": 1.3, "different_type": 1.1, "special_move": [1.0], "types": {}}
    return data


def raid_item(pokemon, form=None, cp=None, korean=None):
    return {"id": pokemon, "form": form, "costume": None, "level": None,
            "names": {"English": pokemon.title(), "Korean": korean},
            "types": ["Fire"], "cpRange": [cp - 80, cp] if cp else None, "shiny": True}


def raid_list():
    return {"currentList": {
        "lvl5": [raid_item("FLAME", "FLAME", catch_cp(200, 150, 200))],
        "lvl3": [raid_item("FLAME", "FLAME_ALOLA", catch_cp(210, 140, 200))],
        "lvl1": [raid_item("MR_LEAF", "MR_LEAF_FESTIVAL_HAT", catch_cp(170, 170, 170))],  # costume
        "mega": [raid_item("WAVE", "WAVE_MEGA", catch_cp(180, 160, 180))],
        "shadow_lvl3": [raid_item("FLAME", "FLAME", catch_cp(200, 150, 200))],
        "lvl9": [raid_item("FLAME", "FLAME")],                                   # unknown level
        "legendary_mega": [raid_item("UNKNOWNMON", "UNKNOWNMON_MEGA")],          # unknown species
        "ex": [raid_item("WAVE", "WAVE_ORIGIN", 9999)],                          # unknown form
        "ultra_beast": [raid_item("NAMELESS", "NAMELESS")],                      # no Korean name
    }}


def max_list(empty=False):
    return {"currentList": [] if empty else {
        "tier_1": [{"id": "WAVE", "names": {"English": "Wave"}, "types": ["Water"],
                    "cpRange": [1, catch_cp(180, 160, 180)], "level": 1}],
        "tier_3": [{"id": "FLAME", "names": {"English": "Alolan Flame"}, "types": ["Fire"],
                    "cpRange": [1, catch_cp(210, 140, 200)], "level": 3}],
    }}


def fake_fetch(raids=None, maxes=None, fail_max=False, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        if url.endswith("raidboss.json"):
            return json.dumps(raids or raid_list()).encode()
        if url.endswith("events.json"):
            from test_events import event_list
            return json.dumps(event_list()).encode()
        if fail_max:
            raise OSError("max list offline")
        return json.dumps(maxes or max_list()).encode()
    return fetch


def by_label(bosses):
    return {(b["source"]["level"], b["source"]["id"]): b for b in bosses}


class ParseAndMapTests(unittest.TestCase):
    def setUp(self):
        self.data = reference()
        self.raids = [raid_bosses.map_boss(self.data, e) for e in raid_bosses.parse_raids(raid_list())]
        self.found = by_label(self.raids)

    def test_species_form_mega_tier_and_shadow(self):
        five = self.found[("lvl5", "FLAME")]
        self.assertEqual((five["status"], five["species"], five["form"], five["tier"], five["shadow"]),
                         ("ok", "불꽃몬", None, "5", False))
        alola = self.found[("lvl3", "FLAME")]
        self.assertEqual((alola["form"], alola["form_name"], alola["types"]), ("Alola", "알로라의 모습", ["불꽃", "풀"]))
        mega = self.found[("mega", "WAVE")]
        self.assertEqual((mega["temp"], mega["tier"], mega["label"], mega["types"]), ("MEGA", "mega", "메가물몬", ["물", "드래곤"]))
        shadow = self.found[("shadow_lvl3", "FLAME")]
        self.assertEqual((shadow["tier"], shadow["shadow"], shadow["label"]), ("3", True, "그림자 불꽃몬"))

    def test_costume_uses_the_regular_stats_when_the_cp_range_matches(self):
        costume = self.found[("lvl1", "MR_LEAF")]
        self.assertEqual((costume["status"], costume["species"], costume["form"], costume["variant"]),
                         ("ok", "풀몬", None, "Festival_Hat"))
        self.assertIn("CP 범위 일치", costume["notes"][0])

    def test_failures_are_kept_with_a_reason(self):
        reasons = {key: b["reason"] for key, b in self.found.items() if b["status"] == "failed"}
        self.assertEqual(set(reasons), {("lvl9", "FLAME"), ("legendary_mega", "UNKNOWNMON"), ("ex", "WAVE"),
                                        ("ultra_beast", "NAMELESS")})
        self.assertIn("등급", reasons[("lvl9", "FLAME")])
        self.assertIn("UNKNOWNMON", reasons[("legendary_mega", "UNKNOWNMON")])
        self.assertIn("Origin", reasons[("ex", "WAVE")])
        self.assertIn("한국어 이름", reasons[("ultra_beast", "NAMELESS")])

    def test_cp_mismatch_is_reported(self):
        item = raid_item("FLAME", "FLAME", catch_cp(200, 150, 200) + 50)
        boss = raid_bosses.map_boss(self.data, raid_bosses.parse_raids({"currentList": {"lvl5": [item]}})[0])
        self.assertEqual(boss["status"], "ok")
        self.assertTrue(any("다릅니다" in note for note in boss["notes"]))

    def test_max_battles_map_tiers_and_regional_names(self):
        maxes = [raid_bosses.map_boss(self.data, e, "max") for e in raid_bosses.parse_max(max_list())]
        self.assertEqual([(b["species"], b["tier"], b["form"]) for b in maxes], [("물몬", "1", None), ("불꽃몬", "3", "Alola")])
        self.assertEqual(raid_bosses.parse_max(max_list(empty=True)), [])

    def test_bad_files_are_rejected(self):
        with self.assertRaises(raid_bosses.BossListError):
            raid_bosses._decode("raidboss.json", b"not json")
        with self.assertRaises(raid_bosses.BossListError):
            raid_bosses._decode("raidboss.json", b'{"list": []}')


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)

    def test_update_keeps_a_dated_copy_with_source_and_hash(self):
        calls = []
        manifest = raid_bosses.update(self.data_dir, fetch=fake_fetch(calls=calls),
                                      now=datetime(2026, 11, 25, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(manifest["version"], "20261125T130000Z-pokemon-go-api")
        self.assertEqual(calls, list(raid_bosses.SOURCES.values()))
        folder = self.data_dir / "raid-bosses" / manifest["version"]
        self.assertTrue((folder / "raidboss.json").is_file())
        saved = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["files"]["raidboss.json"]["url"], raid_bosses.SOURCES["raidboss.json"])
        self.assertEqual(len(saved["files"]["raidboss.json"]["sha256"]), 64)
        listing = raid_bosses.current(self.data_dir)
        self.assertEqual(len(listing["raids"]), 9)
        self.assertEqual(len(listing["max_battles"]), 2)

    def test_failed_download_keeps_the_previous_list(self):
        first = raid_bosses.update(self.data_dir, fetch=fake_fetch(),
                                   now=datetime(2026, 11, 24, tzinfo=timezone.utc))

        def offline(url):
            raise OSError("offline")
        with self.assertRaises(OSError):
            raid_bosses.update(self.data_dir, fetch=offline)
        with self.assertRaises(raid_bosses.BossListError):
            raid_bosses.update(self.data_dir, fetch=fake_fetch(raids={"currentList": {}}))
        self.assertEqual(raid_bosses.current(self.data_dir)["manifest"]["version"], first["version"])

    def test_missing_max_list_is_recorded_not_fatal(self):
        manifest = raid_bosses.update(self.data_dir, fetch=fake_fetch(fail_max=True))
        self.assertIn("maxbattles.json", manifest["errors"])
        self.assertEqual(raid_bosses.current(self.data_dir)["max_battles"], [])

    def test_old_versions_are_pruned(self):
        for day in range(raid_bosses.KEEP_VERSIONS + 3):
            raid_bosses.update(self.data_dir, fetch=fake_fetch(),
                               now=datetime(2026, 10, 1 + day, tzinfo=timezone.utc))
        folders = [p for p in (self.data_dir / "raid-bosses").iterdir() if p.is_dir()]
        self.assertEqual(len(folders), raid_bosses.KEEP_VERSIONS)


class RecommendCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        self.data = reference()
        self.box = BoxStore(self.data_dir)
        self.add(1, "물몬", "SPLASH_FAST", "WAVE")
        raid_bosses.update(self.data_dir, fetch=fake_fetch())
        self.listing = raid_bosses.mapped(self.data, raid_bosses.current(self.data_dir))

    def add(self, number, species, fast, charged, **extra):
        self.box.approve("scan", number, {"species": species, "iv": {"attack": 15, "defense": 15, "stamina": 15},
                                          "level": {"value": 40.0}, **extra,
                                          "moves": {"fast": {"move_id": fast}, "charged": [{"move_id": charged}]}},
                         {"scan_id": "scan", "individual": number})

    def boss(self, level, pokemon="FLAME", kind="raids"):
        return next(b for b in self.listing[kind] if b["source"]["level"] == level and b["source"]["id"] == pokemon)

    def test_result_is_cached_until_the_box_or_options_change(self):
        boss = self.boss("lvl5")
        first = raid_bosses.recommend(self.data_dir, self.data, self.box.list(), boss, {})
        self.assertFalse(first["cached"])
        self.assertEqual([m["name"] for m in first["summary"]["party"]], ["물몬"])
        again = raid_bosses.recommend(self.data_dir, self.data, self.box.list(), boss, {})
        self.assertTrue(again["cached"])
        self.assertEqual(again["summary"], first["summary"])
        rainy = raid_bosses.recommend(self.data_dir, self.data, self.box.list(), boss, {"weather": "RAINY"})
        self.assertFalse(rainy["cached"])
        self.add(2, "풀몬", "LEAF_FAST", "BLADE")
        changed = raid_bosses.recommend(self.data_dir, self.data, self.box.list(), boss, {})
        self.assertFalse(changed["cached"])
        self.assertEqual(len(changed["summary"]["party"]), 2)

    def test_shadow_mega_and_max_bosses(self):
        shadow = raid_bosses.recommend(self.data_dir, self.data, self.box.list(), self.boss("shadow_lvl3"), {})
        self.assertEqual(shadow["summary"]["shadow"]["enrage_at"], 0.6)
        self.assertIsNotNone(shadow["summary"]["shadow"]["comparison"])
        mega = raid_bosses.recommend(self.data_dir, self.data, self.box.list(), self.boss("mega", "WAVE"),
                                     {"include_mega": True})
        self.assertEqual(mega["options"]["mega"], "all")
        self.assertIn("mega_comparison", mega["summary"])
        evolved = raid_bosses.recommend(self.data_dir, self.data, self.box.list(), self.boss("mega", "WAVE"),
                                        {"mega": "evolved"})
        self.assertEqual(evolved["options"]["mega"], "evolved")
        self.add(3, "물몬", "SPLASH_FAST", "WAVE", max_state="dynamax")
        maxed = raid_bosses.recommend(self.data_dir, self.data, self.box.list(), self.boss("tier_3", kind="max_battles"),
                                      {"trainers": 9})
        self.assertEqual(maxed["options"]["trainers"], 4)
        self.assertEqual(maxed["summary"]["party"][0]["max_move"], "다이스트림")

    def test_failed_boss_and_bad_options_raise(self):
        from boxscan import raid
        with self.assertRaises(raid.RaidError):
            raid_bosses.recommend(self.data_dir, self.data, self.box.list(), self.boss("lvl9"), {})
        with self.assertRaises(raid.RaidError):
            raid_bosses.recommend(self.data_dir, self.data, self.box.list(), self.boss("lvl5"), {"weather": "SUNNY"})
        with self.assertRaises(raid.RaidError):
            raid_bosses.recommend(self.data_dir, self.data, self.box.list(), self.boss("lvl5"), {"trainers": 0})


class CurrentRaidApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from boxscan import reference as reference_module
        from boxscan.api import create_app
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        self.calls = []
        self.fetch = fake_fetch(calls=self.calls)
        self.client = TestClient(create_app(self.data_dir, raid_fetch=lambda url: self.fetch(url)),
                                 base_url="http://localhost")
        self.addCleanup(self.client.close)
        folder = self.data_dir / "reference" / "t"
        folder.mkdir(parents=True)
        (folder / "reference.json").write_text(json.dumps(reference_module.to_json(reference())), encoding="utf-8")
        (self.data_dir / "reference" / "current").write_text("t", encoding="utf-8")
        BoxStore(self.data_dir).approve(
            "scan", 1, {"species": "물몬", "iv": {"attack": 15, "defense": 15, "stamina": 15}, "level": {"value": 40.0},
                        "moves": {"fast": {"move_id": "SPLASH_FAST"}, "charged": [{"move_id": "WAVE"}]}},
            {"scan_id": "scan", "individual": 1})

    def test_ai_export_has_the_box_and_current_bosses(self):
        before = self.client.get("/api/exports/box-ai.md")
        self.assertEqual(before.status_code, 200)
        self.assertTrue(before.headers["content-type"].startswith("text/markdown"))
        self.assertIn('filename="pokemon-box-ai-', before.headers["content-disposition"])
        self.assertIn("저장된 현재 보스 목록이 없습니다", before.text)
        self.client.post("/api/raids/current/refresh")
        text = self.client.get("/api/exports/box-ai.md").text
        self.assertIn("| 01 | 물몬 |", text)
        self.assertIn("· 레이드 5성", text)
        self.assertIn("· 맥스배틀", text)
        self.assertIn("- 추천: 물몬(01번", text)
        self.assertNotIn("현재 레이드·맥스배틀 추천", self.client.get("/api/exports/box-ai.md?raids=false").text)
        self.assertEqual(self.calls[3:], [])  # the export never downloads

    def test_list_refresh_and_recommend(self):
        empty = self.client.get("/api/raids/current").json()
        self.assertIsNone(empty["version"])
        self.assertEqual(self.calls, [])  # opening the page never downloads
        listing = self.client.post("/api/raids/current/refresh").json()
        self.assertEqual(len(self.calls), 3)  # raid list, max battle list, event calendar
        self.assertIsNone(listing["events_error"])
        calendar = self.client.get("/api/events").json()
        self.assertEqual(calendar["credit"], "ScrapedDuck · LeekDuck")
        self.assertIn("Mega Wave in Mega Raids", [e["name"] for e in calendar["events"]])
        self.assertEqual(listing["failed"], 4)
        ordered = [(b["tier"], b["shadow"]) for b in listing["raids"] if b["status"] == "ok"]
        self.assertEqual(ordered, [("5", False), ("mega", False), ("3", False), ("1", False), ("3", True)])
        self.assertEqual(len(listing["raids"]), 9)  # failures are listed too
        self.assertEqual(len(listing["max_battles"]), 2)
        self.assertEqual(self.client.get("/api/raids/current").json()["version"], listing["version"])
        self.client.get("/api/events")
        self.assertEqual(len(self.calls), 3)  # reading the list or the calendar never downloads

        boss = next(b for b in listing["raids"] if b["status"] == "ok" and b["tier"] == "5")
        body = {"version": listing["version"], "kind": "raid", "index": boss["index"]}
        first = self.client.post("/api/raids/current/recommend", json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["cached"])
        self.assertTrue(self.client.post("/api/raids/current/recommend", json=body).json()["cached"])
        failed = next(b for b in listing["raids"] if b["status"] == "failed")
        self.assertEqual(self.client.post("/api/raids/current/recommend",
                                          json={**body, "index": failed["index"]}).status_code, 422)
        self.assertEqual(self.client.post("/api/raids/current/recommend",
                                          json={**body, "index": 999}).status_code, 404)
        self.assertEqual(self.client.post("/api/raids/current/recommend",
                                          json={**body, "version": "old"}).status_code, 409)
        self.assertEqual(self.client.post("/api/raids/current/recommend", json=body,
                                          headers={"Origin": "http://evil.example"}).status_code, 403)

    def test_failed_refresh_keeps_the_list_and_says_why(self):
        self.client.post("/api/raids/current/refresh")

        def offline(url):
            raise OSError("offline")
        self.fetch = offline
        response = self.client.post("/api/raids/current/refresh")
        self.assertEqual(response.status_code, 502)
        self.assertIn("기존 목록을 유지", response.json()["detail"])
        self.assertIsNotNone(self.client.get("/api/raids/current").json()["version"])

    def test_page_and_navigation(self):
        page = self.client.get("/raids")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/assets/raids.js", page.text)
        self.assertIn("보스 목록·이벤트 갱신", page.text)
        self.assertIn("/assets/calendar.js", page.text)
        for asset in ("/assets/raids.js", "/assets/calendar.js"):
            self.assertEqual(self.client.get(asset).status_code, 200)
        for path in ("/", "/box", "/raid", "/max"):
            self.assertIn('href="/raids">레이드·이벤트</a>', self.client.get(path).text)


if __name__ == "__main__":
    unittest.main()

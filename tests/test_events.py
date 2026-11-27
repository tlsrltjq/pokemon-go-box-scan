import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import events

from test_raid import reference

NOW = datetime(2026, 11, 26, 18, 30)


def event_list() -> list:
    """A synthetic events.json in ScrapedDuck's shape (fake species names: Flame, Wave, Leaf)."""
    return [
        {"eventID": "mega-wave", "name": "Mega Wave in Mega Raids", "eventType": "raid-battles", "heading": "Raid Battles",
         "link": "https://leekduck.com/events/mega-wave/", "image": "https://example.test/a.jpg",
         "start": "2026-11-25T06:00:00.000", "end": "2026-12-01T22:00:00.000",
         "extraData": {"raidbattles": {"bosses": [{"name": "Mega Wave", "canBeShiny": True},
                                                  {"name": "Shadow Flame (Incarnate)", "canBeShiny": False},
                                                  {"name": "Unknownmon"}], "shinies": []},
                       "generic": {"hasSpawns": False}}},
        {"eventID": "raidhour", "name": "Flame Raid Hour", "eventType": "raid-hour", "heading": "Raid Hour",
         "start": "2026-11-26T18:00:00.000", "end": "2026-11-26T19:00:00.000", "extraData": {}},
        {"eventID": "spotlight", "name": "Leaf Spotlight Hour", "eventType": "pokemon-spotlight-hour",
         "start": "2026-12-02T18:00:00.000", "end": "2026-12-02T19:00:00.000",
         "extraData": {"spotlight": {"name": "Alolan Leaf", "bonus": "2× Catch Candy", "canBeShiny": True}}},
        {"eventID": "cd", "name": "Wave Community Day", "eventType": "community-day",
         "start": "2026-12-06T14:00:00.000", "end": "2026-12-06T17:00:00.000",
         "extraData": {"communityday": {"spawns": [{"name": "Wave"}], "bonuses": [{"text": "3× Stardust"}]}}},
        {"eventID": "gbl", "name": "GO Battle League", "eventType": "go-battle-league",
         "start": "2026-12-01T21:00:00.000Z", "end": "2026-12-08T21:00:00.000Z"},
        {"eventID": "old", "name": "Old Event", "eventType": "event", "start": "2026-11-01T10:00:00.000",
         "end": "2026-11-10T10:00:00.000"},
        {"eventID": "tba", "name": "Announced", "eventType": "event", "start": None, "end": None},
        "junk", {"name": "no id"}, {"eventID": "bad", "name": "Bad date", "start": "soon", "end": "later"},
    ]


def fetch_events(calls=None, data=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        return json.dumps(event_list() if data is None else data).encode()
    return fetch


class EventTests(unittest.TestCase):
    def test_parse_keeps_dated_events_and_reads_local_and_utc_times(self):
        parsed = {e["id"]: e for e in events.parse(json.dumps(event_list()).encode())}
        self.assertEqual(sorted(parsed), ["cd", "gbl", "mega-wave", "old", "raidhour", "spotlight"])
        self.assertEqual(parsed["raidhour"]["start"], datetime(2026, 11, 26, 18, 0))  # local wall-clock time
        utc = datetime(2026, 12, 1, 21, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        self.assertEqual(parsed["gbl"]["start"], utc)  # "Z" times become this computer's local time
        for bad in (b"not json", b'{"events": []}'):
            with self.subTest(bad=bad), self.assertRaises(events.EventListError):
                events.parse(bad)

    def test_view_orders_by_status_and_maps_pokemon_to_korean(self):
        listing = {"manifest": {"version": "v", "source": "ScrapedDuck", "source_home": "h", "fetched_at": "t"},
                   "events": events.parse(json.dumps(event_list()).encode())}
        view = events.view(listing, reference(), now=NOW)
        ids = [e["id"] for e in view["events"]]
        self.assertNotIn("old", ids)  # ended more than a day ago
        self.assertEqual(ids[:2], ["mega-wave", "raidhour"])  # on now, by start
        by_id = {e["id"]: e for e in view["events"]}
        self.assertEqual((by_id["raidhour"]["status"], by_id["spotlight"]["status"]), ("live", "upcoming"))
        mega = by_id["mega-wave"]
        self.assertEqual((mega["type_name"], mega["raid"]), ("레이드 보스", True))
        self.assertEqual([b["name"] for b in mega["bosses"]], ["메가물몬", "그림자 불꽃몬", "Unknownmon"])
        self.assertEqual((mega["bosses"][0]["temp"], mega["bosses"][0]["shiny"], mega["bosses"][0]["dex"]), ("MEGA", True, 2))
        self.assertEqual((mega["bosses"][1]["species"], mega["bosses"][1]["shadow"]), ("불꽃몬", True))
        self.assertFalse(mega["bosses"][2]["mapped"])  # unknown names stay as published
        spotlight = by_id["spotlight"]["spotlight"]
        self.assertEqual((spotlight["name"], spotlight["bonus"]), ("풀몬 (알로라)", "2× Catch Candy"))
        self.assertEqual((by_id["cd"]["spawns"], by_id["cd"]["bonuses"]), (["물몬"], ["3× Stardust"]))
        self.assertEqual(by_id["gbl"]["type_name"], "GO 배틀리그")
        self.assertEqual(by_id["raidhour"]["start"], "2026-11-26T18:00")
        self.assertEqual(events.view(None, reference()), {"version": None, "events": []})
        self.assertEqual(events.pokemon("Mega Wave Y", reference())["name"], "메가물몬Y")
        self.assertEqual(events.pokemon("Primal Leaf", reference())["name"], "원시풀몬")

    def test_update_keeps_the_previous_calendar_when_a_download_fails(self):
        with TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            self.assertIsNone(events.current(data_dir))
            calls = []
            manifest = events.update(data_dir, fetch=fetch_events(calls), now=datetime(2026, 11, 26, tzinfo=timezone.utc))
            self.assertEqual(calls, [events.SOURCE_URL])
            self.assertEqual((manifest["count"], manifest["credit"]), (6, events.CREDIT))
            stored = events.current(data_dir)
            self.assertEqual(stored["manifest"]["version"], manifest["version"])
            self.assertEqual(len(stored["events"]), 6)

            def offline(url):
                raise OSError("offline")
            for broken in (offline, fetch_events(data={"x": 1}), fetch_events(data=[])):
                with self.subTest(broken=broken), self.assertRaises((OSError, ValueError)):
                    events.update(data_dir, fetch=broken, now=datetime(2026, 11, 27, tzinfo=timezone.utc))
            self.assertEqual(events.current(data_dir)["manifest"]["version"], manifest["version"])
            for day in range(1, 15):  # old downloads are pruned
                events.update(data_dir, fetch=fetch_events(), now=datetime(2026, 12, day, tzinfo=timezone.utc))
            folders = [p for p in events.root(data_dir).iterdir() if p.is_dir()]
            self.assertEqual(len(folders), events.KEEP_VERSIONS)


if __name__ == "__main__":
    unittest.main()

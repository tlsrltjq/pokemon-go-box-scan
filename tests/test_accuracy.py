import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import accuracy
from boxscan.box import BoxStore

RESOLVED = "RESOLVED_BY_CONSTRAINTS"
IV = {"attack": 15, "defense": 14, "stamina": 13}


def read(value, votes=3, observations=3):
    return {"value": value, "votes": votes, "observations": observations}


def candidate(frames, species, cp, hp, fast="A", charged=("B",), status=RESOLVED, iv=IV, code=None, **extra):
    """A synthetic analysis.json individual in the shape analysis.assemble writes."""
    return {
        "frames": frames,
        "species": read(species) if species else None,
        "cp": read(cp),
        "max_hp": read(hp),
        "appraisals": [{"iv": iv}] if iv else [],
        "moves": {"fast": {"value": fast, "name": fast, "match": "exact"} if fast else None,
                  "charged": [{"value": name, "name": name, "match": "exact"} for name in charged]},
        "level": {"status": status},
        "code": read(code) if code else None,
        **extra,
    }


def values(species, cp, hp, fast="A", charged=("B",), iv=IV):
    return {"species": species, "cp": cp, "max_hp": hp, "iv": dict(iv),
            "moves": {"fast": {"name": fast}, "charged": [{"name": name} for name in charged]}}


class AccuracyTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.box = BoxStore(self.root)

    def write(self, scan_id, individuals):
        (self.root / scan_id).mkdir()
        (self.root / scan_id / "analysis.json").write_text(
            json.dumps({"individuals": individuals}, ensure_ascii=False), encoding="utf-8")

    def approve(self, scan_id, index, frames, saved, target=None, revision=None):
        return self.box.approve(scan_id, index, saved, {"scan_id": scan_id, "individual": index, "frames": frames},
                                target, revision)

    def test_compares_saved_values_and_simulates_automatic_saving(self):
        self.write("s1", [
            candidate([1, 2], "하나몬", 500, 80),
            candidate([3, 4], "둘몬", 349, 80, status="CONFLICT"),  # CP misread; level does not fit
            candidate([5, 6], "셋몬", 700, 90, charged=("B",)),     # passes the rules, charged move wrong
            candidate([7, 8], "넷몬", 800, 95),                     # never saved
        ])
        first = self.approve("s1", 0, [1, 2], values("하나몬", 500, 80))
        second = self.approve("s1", 1, [3, 4], values("둘몬", 3492, 80))
        self.approve("s1", 2, [5, 6], values("셋몬", 700, 90, charged=("C",)))
        # Found later in the box: the max HP was wrong too. The edited value is the reference.
        self.box.edit(second["id"], {**self.box.get(second["id"]), "max_hp": 81}, second["revision"])

        # Rescan of 하나몬 01. The scan was analyzed again since, so its candidate moved to index 1.
        self.write("s2", [
            candidate([1, 2], None, 100, 10),
            candidate([5, 6], "하나몬", 600, 80, code="01"),
        ])
        self.approve("s2", 0, [5, 6], values("하나몬", 600, 80), target=first["id"], revision=1)

        report = accuracy.measure(self.root)
        self.assertEqual((report["scans"], report["candidates"], report["approved"]), (2, 6, 4))
        self.assertEqual((report["unaligned"], report["edited_after_approval"]), (0, 1))
        self.assertEqual(report["fields"]["cp"]["wrong"], 1)
        self.assertEqual(report["fields"]["cp"]["correct"], 3)
        self.assertEqual(report["fields"]["max_hp"]["wrong"], 1)
        self.assertEqual(report["fields"]["charged"]["wrong"], 1)
        auto = report["auto"]
        self.assertEqual((auto["pass"], auto["pass_wrong"], auto["flagged"], auto["flagged_wrong"]), (3, 1, 1, 1))
        self.assertEqual(auto["reasons"]["레벨 CONFLICT"], 1)
        self.assertEqual(report["unsaved"], {"count": 2, "would_pass": 1})
        self.assertEqual(report["matching"], {"updates": 1, "code_read": 1, "code_same": 1, "code_other": 0})
        wrong = {(m["scan_id"], m["individual"], m["field"]) for m in report["mismatches"]}
        self.assertEqual(wrong, {("s1", 1, "cp"), ("s1", 1, "max_hp"), ("s1", 2, "charged")})

        text = "\n".join(accuracy.format_report(report, details=True))
        self.assertIn("자동 저장 3/4", text)
        self.assertIn("읽음 349 → 저장 3492", text)
        json.dumps(accuracy.to_json(report), ensure_ascii=False)

    def test_box_is_opened_read_only_and_missing_box_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            accuracy.measure(self.root / "nothing")
        self.write("s1", [candidate([1], "하나몬", 500, 80)])
        self.approve("s1", 0, [1], values("하나몬", 500, 80))
        before = (self.root / "box.sqlite3").read_bytes()
        accuracy.measure(self.root)
        self.assertEqual((self.root / "box.sqlite3").read_bytes(), before)

    def test_rules_flag_merged_and_split_individuals(self):
        merged = candidate([1, 2], "하나몬", 500, 80)
        merged["appraisals"].append({"iv": {"attack": 1, "defense": 2, "stamina": 3}})
        self.assertIn("한 개체에 IV가 여러 개 (두 마리가 합쳐졌을 수 있음)", accuracy.gate(merged))
        twins = [candidate([1], "하나몬", 500, 80), candidate([2], "하나몬", 510, 80)]
        self.assertEqual(accuracy._duplicates(twins), {0, 1})
        shaky = candidate([1], "하나몬", 500, 80)
        shaky["cp"] = read(500, votes=1, observations=3)
        self.assertIn("CP 판독 일치율 낮음", accuracy.gate(shaky))
        suspect = candidate([1], "하나몬", 500, 80)
        suspect["moves"]["charged"][0]["match"] = "none"
        self.assertIn("배울 수 없거나 사전에 없는 기술", accuracy.gate(suspect))
        self.assertEqual(accuracy.gate(candidate([1], "하나몬", 500, 80)), [])

    def test_compare_separates_missing_from_wrong(self):
        saved = accuracy.saved_values(values("하나몬", 500, 80, charged=("B", "C")), mega=False)
        read_values = accuracy.candidate_values(candidate([1], "하나몬", 500, None, charged=("B",)))
        result = accuracy.compare(read_values, saved)
        self.assertEqual((result["species"], result["max_hp"], result["charged"]), ("correct", "missing", "missing"))
        self.assertEqual(result["max_state"], "empty")

    def test_mega_candidate_is_compared_with_the_screen_values(self):
        saved = {**values("하나몬", 500, 80), "temp_observation": {"cp": 900, "max_hp": 120}}
        mega = candidate([1], "하나몬", 900, 120, state={"temp_evolution": "MEGA"})
        result = accuracy.compare(accuracy.candidate_values(mega), accuracy.saved_values(saved, mega=True))
        self.assertEqual((result["cp"], result["max_hp"]), ("correct", "correct"))


if __name__ == "__main__":
    unittest.main()

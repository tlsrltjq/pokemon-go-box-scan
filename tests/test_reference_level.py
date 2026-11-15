import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import level, reference
from boxscan.reference import BaseStats, Reference

# Synthetic values only. Real game data is downloaded locally and never used in tests.
LEVELS = [1 + step / 2 for step in range(0, 99)]  # 1.0 ... 50.0
CPM = {value: round(0.1 + 0.014 * (value - 1), 6) for value in LEVELS}
IV = {"attack": 10, "defense": 5, "stamina": 12}


def synthetic_files(count=120, cpm=CPM) -> dict[str, bytes]:
    stats = [{"pokemon_id": n, "pokemon_name": f"Mon{n}", "form": "Normal",
              "base_attack": 100 + n, "base_defense": 90 + n, "base_stamina": 120 + n}
             for n in range(1, count + 1)]
    stats.append({"pokemon_id": 2, "pokemon_name": "Mon2", "form": "Other",
                  "base_attack": 300, "base_defense": 50, "base_stamina": 60})
    names = ["pokemon_species_id,local_language_id,name,genus"]
    names += [f"{n},3,테스트몬{n},종류" for n in range(1, count + 1)]
    names += [f"{n},9,Mon{n},Kind" for n in range(1, count + 1)]
    return {
        "pokemon_stats.json": json.dumps(stats).encode(),
        "cp_multiplier.json": json.dumps([{"level": k, "multiplier": v} for k, v in cpm.items()]).encode(),
        "pokemon_species_names.csv": "\n".join(names).encode(),
    }


def synthetic_reference() -> Reference:
    return reference.parse(synthetic_files(), "test", "2026-01-01T00:00:00+00:00")


class ReferenceTests(unittest.TestCase):
    def test_parse_maps_korean_names_to_forms(self):
        data = synthetic_reference()
        self.assertEqual([f.form for f in data.forms("테스트몬2")], ["Normal", "Other"])
        self.assertEqual(data.forms("Mon2"), [])
        self.assertEqual(data.forms("없는몬"), [])

    def test_parse_rejects_incomplete_data(self):
        with self.assertRaises(ValueError):
            reference.parse(synthetic_files(count=5))
        with self.assertRaises(ValueError):
            reference.parse(synthetic_files(cpm={1.0: 0.1}))
        broken = synthetic_files()
        broken["pokemon_stats.json"] = b'[{"pokemon_id": 1}]'
        with self.assertRaises(ValueError):
            reference.parse(broken)

    def test_update_keeps_previous_version_when_new_data_is_invalid(self):
        with TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            files = synthetic_files()
            by_url = {url: files[name] for name, url in reference.SOURCES.items()}
            manifest = reference.update(data_dir, fetch=by_url.__getitem__)
            folder = reference.root(data_dir) / manifest["version"]
            self.assertEqual(json.loads((folder / "manifest.json").read_text())["version"],
                             manifest["version"])
            self.assertEqual(len(manifest["files"]["pokemon_stats.json"]["sha256"]), 64)
            with self.assertRaises(ValueError):
                reference.update(data_dir, fetch=lambda url: b"not json")
            self.assertEqual(reference.load(data_dir).version, manifest["version"])
            self.assertIsNone(reference.load(data_dir / "empty"))


class LevelTests(unittest.TestCase):
    def setUp(self):
        self.data = synthetic_reference()
        self.base = self.data.forms("테스트몬40")[0]

    def observed(self, at_level):
        multiplier = CPM[at_level]
        return level.cp(self.base, IV, multiplier), level.hp(self.base, IV, multiplier)

    def test_cp_and_hp_resolve_one_level(self):
        cp, hp = self.observed(27.5)
        result = level.estimate(self.data, "테스트몬40", IV, cp, hp)
        self.assertEqual(result["status"], level.RESOLVED)
        self.assertEqual(result["levels"], [27.5])
        self.assertEqual(result["form"], "Normal")
        self.assertTrue(result["used_max_hp"])

    def test_wrong_iv_is_reported_as_conflict(self):
        cp, hp = self.observed(27.5)
        wrong = {**IV, "attack": IV["attack"] - 3}
        self.assertEqual(level.estimate(self.data, "테스트몬40", wrong, cp, hp)["status"], level.CONFLICT)

    def test_clamped_low_values_stay_ambiguous(self):
        tiny = BaseStats(999, "Tiny", "Normal", 1, 1, 1)
        data = Reference("test", "", {999: [tiny]}, CPM, {"작은몬": 999})
        zero = {"attack": 0, "defense": 0, "stamina": 0}
        result = level.estimate(data, "작은몬", zero, 10, 10)
        self.assertEqual(result["status"], level.AMBIGUOUS)
        self.assertGreater(len(result["levels"]), 1)

    def test_other_form_is_shown_when_only_it_matches(self):
        other = self.data.forms("테스트몬2")[1]
        multiplier = CPM[20.0]
        cp, hp = level.cp(other, IV, multiplier), level.hp(other, IV, multiplier)
        result = level.estimate(self.data, "테스트몬2", IV, cp, hp)
        self.assertEqual(result["form"], "Other")
        self.assertEqual(result["levels"], [20.0])

    def test_missing_inputs_and_reference(self):
        self.assertEqual(level.estimate(None, "테스트몬1", IV, 100, 50)["status"], level.NO_REFERENCE)
        missing = level.estimate(self.data, None, None, 100, None)
        self.assertEqual(missing["status"], level.MISSING_INPUT)
        self.assertEqual(missing["missing"], ["species", "iv"])
        self.assertEqual(level.estimate(self.data, "없는몬", IV, 100, 50)["status"], level.UNKNOWN_SPECIES)

import json
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import level, reference
from boxscan.reference import BaseStats, Reference

# Synthetic values only. Real game data is downloaded locally and never used in tests.
WHOLE = [round(0.1 + 0.014 * (level - 1), 6) for level in range(1, 81)]  # game master lists 80
IV = {"attack": 10, "defense": 5, "stamina": 12}
MOVE_COUNT = 110


def master(count: int, whole=WHOLE) -> list[dict]:
    items = [{"templateId": "PLAYER_LEVEL_SETTINGS",
              "data": {"playerLevel": {"cpMultiplier": whole}}},
             {"templateId": "ITEM_POTION", "data": {"itemSettings": {}}},
             {"templateId": "BATTLE_SETTINGS", "data": {"battleSettings": {
                 "sameTypeAttackBonusMultiplier": 1.2, "maximumEnergy": 100, "energyDeltaPerHealthLost": 0.5,
                 "swapDurationMs": 1000, "enemyAttackInterval": 1.5, "bossEnergyRegenerationPerHealthLost": 0.5}}},
             {"templateId": "WEATHER_AFFINITY_CLEAR", "data": {"weatherAffinities": {
                 "weatherCondition": "CLEAR", "pokemonType": ["POKEMON_TYPE_FIRE"]}}},
             {"templateId": "MEGA_EVO_SETTINGS", "data": {"megaEvoSettings": {
                 "attackBoostFromMegaSameType": 1.3, "attackBoostFromMegaDifferentType": 1.1,
                 "tempEvoSpecialAttackBoosts": [{"gymsAndRaidsMultiplier": 1.0}, {"gymsAndRaidsMultiplier": 1.1}]}}},
             {"templateId": "PRIMAL_EVO_SETTINGS", "data": {"primalEvoSettings": {"typeBoosts": [
                 {"pokemonId": "MON5", "boostType": ["POKEMON_TYPE_FIRE", "POKEMON_TYPE_GRASS"]}]}}}]
    for attack in reference.TYPE_ORDER:  # synthetic type chart: everything neutral
        items.append({"templateId": f"POKEMON_TYPE_{attack}", "data": {"typeEffective": {
            "attackType": f"POKEMON_TYPE_{attack}", "attackScalar": [1.0] * 18}}})

    # 110 synthetic moves: MOVE_1_FAST..MOVE_10_FAST and MOVE_11..MOVE_110, plus numeric id 406.
    for n in range(1, MOVE_COUNT + 1):
        name = f"MOVE_{n}_FAST" if n <= 10 else f"MOVE_{n}"
        items.append({"templateId": f"V{n:04d}_MOVE_{name}",
                      "data": {"moveSettings": {"movementId": n, "power": float(n), "pokemonType": "POKEMON_TYPE_NORMAL",
                                                "durationMs": 1000, "damageWindowStartMs": 500,
                                                "energyDelta": 5 if n <= 10 else -50}}})
    items.append({"templateId": "V0406_MOVE_SPECIAL_BEAM_FIRE",
                  "data": {"moveSettings": {"movementId": 406, "power": 77.0}}})

    def pokemon(number, suffix, form, attack, defense, stamina):
        template = f"V{number:04d}_POKEMON_MON{number}{suffix}"
        settings = {"pokemonId": f"MON{number}",
                    "stats": {"baseAttack": attack, "baseDefense": defense, "baseStamina": stamina},
                    "quickMoves": ["MOVE_1_FAST"], "cinematicMoves": ["MOVE_11", "MOVE_12"],
                    "eliteQuickMove": ["MOVE_2_FAST"], "eliteCinematicMove": ["MOVE_13", 406],
                    "shadow": {"purifiedChargeMove": "MOVE_14", "shadowChargeMove": "MOVE_15"}}
        if form:
            settings["form"] = form
        return {"templateId": template, "data": {"pokemonSettings": settings}}

    for n in range(1, count + 1):
        items.append(pokemon(n, "", None, 100 + n, 90 + n, 120 + n))
        items.append(pokemon(n, "_NORMAL", f"MON{n}_NORMAL", 100 + n, 90 + n, 120 + n))
    items.append(pokemon(2, "_COSTUME_2020", "MON2_COSTUME_2020", 102, 92, 122))  # same stats
    items.append(pokemon(2, "_OTHER", "MON2_OTHER", 300, 50, 60))
    # MON5 can mega evolve; MON6 fuses with MON7 (two duplicate entries) and the fused form
    # replaces MOVE_11 with MOVE_20.
    items[-1 - 2 * (count - 5) - 2]["data"]["pokemonSettings"]["tempEvoOverrides"] = [
        {"tempEvoId": "TEMP_EVOLUTION_MEGA", "stats": {"baseAttack": 250, "baseDefense": 150, "baseStamina": 125}}]
    fuse = {"availableForm": ["MON6_FUSED"],
            "componentPokemonSettings": {"pokedexId": "MON7", "formChangeType": "FUSE"},
            "moveReassignment": {"cinematicMoves": [{"existingMoves": ["MOVE_11"], "replacementMoves": ["MOVE_20"]}]}}
    items[-1 - 2 * (count - 6) - 2]["data"]["pokemonSettings"]["formChange"] = [fuse, dict(fuse)]
    items.append(pokemon(6, "_FUSED", "MON6_FUSED", 200, 180, 160))
    items.append({"templateId": "VM_MOVE_TEMP_EVOLUTION_MEGA_V0005_POKEMON_MON5",
                  "data": {"moveSettings": {"movementId": 900, "power": 140.0, "vfxName": "move_12"}}})
    # Max moves: a Max Attack, a G-Max move for MON3, Max Guard and Max Spirit, and their mappings.
    for template, kind, vfx in (("VN_BM_001", "FIRE", "max_flare"), ("VN_BM_019", "FIRE", "gmax_wildfire"),
                                ("VN_BM_052", "NORMAL", "max_shield"), ("VN_BM_053", "NORMAL", "max_heal")):
        items.append({"templateId": template, "data": {"moveSettings": {
            "movementId": template, "pokemonType": f"POKEMON_TYPE_{kind}", "vfxName": vfx}}})
    items.append({"templateId": "BREAD_MOVE_MAPPING_SETTINGS", "data": {"breadMoveMappings": {
        "mappings": [{"type": "POKEMON_TYPE_FIRE", "move": "VN_BM_001"}]}}})
    items.append({"templateId": "SOURDOUGH_MOVE_MAPPING_SETTINGS", "data": {"sourdoughMoveMappingSettings": {
        "mappings": [{"pokemonId": "MON3", "move": "VN_BM_019"},
                     {"pokemonId": "MON4", "form": "MON4_NORMAL", "move": "VN_BM_019"},
                     {"pokemonId": "UNKNOWN", "move": "VN_BM_019"}]}}})
    return items


def synthetic_files(count=120, whole=WHOLE, check=None) -> dict[str, bytes]:
    game_master = master(count, whole)
    if check is None:
        derived = reference._master_cpm(game_master)
        check = {level: value for level, value in derived.items() if level <= 45}
    fallback = [{"pokemon_id": count + 1, "pokemon_name": "Late", "form": "Normal",
                 "base_attack": 150, "base_defense": 150, "base_stamina": 150},
                {"pokemon_id": 1, "pokemon_name": "Mon1", "form": "Normal",
                 "base_attack": 1, "base_defense": 1, "base_stamina": 1}]  # ignored: game master wins
    names = ["pokemon_species_id,local_language_id,name,genus"]
    names += [f"{n},3,테스트몬{n},종류" for n in range(1, count + 2)]
    names += [f"{n},9,Mon{n},Kind" for n in range(1, count + 2)]
    moves = ["move_id,local_language_id,name"]
    moves += [f"{n},9,Move {n}" for n in range(1, MOVE_COUNT + 1)]
    moves += [f"{n},3,기술{n}번" for n in range(1, MOVE_COUNT + 1)]
    moves += ["999,9,Special Beam", "999,3,특수광선"]
    moves += ["2000,9,Max Flare", "2000,3,다이번"]
    return {
        "game_master.json": json.dumps(game_master).encode(),
        "pokemon_stats.json": json.dumps(fallback).encode(),
        "cp_multiplier.json": json.dumps([{"level": k, "multiplier": v} for k, v in check.items()]).encode(),
        "pokemon_species_names.csv": "\n".join(names).encode(),
        "move_names.csv": "\n".join(moves).encode(),
    }


def synthetic_reference() -> Reference:
    return reference.parse(synthetic_files(), "test", "2026-01-01T00:00:00+00:00")


CPM = synthetic_reference().cpm


class ReferenceTests(unittest.TestCase):
    def test_parse_combines_sources_and_merges_identical_forms(self):
        data = synthetic_reference()
        self.assertEqual([f.form for f in data.forms("테스트몬2")], ["Normal", "Other"])
        self.assertEqual(data.forms("테스트몬1")[0].attack, 101)
        self.assertEqual(data.forms("테스트몬121")[0].name, "Late")
        self.assertEqual(data.notes["stats_from_pogoapi"], 1)
        self.assertEqual(data.forms("Mon2"), [])
        self.assertEqual(data.forms("없는몬"), [])

    def test_moves_join_korean_names_learnsets_and_power(self):
        data = synthetic_reference()
        self.assertEqual(data.move_names["MOVE_1_FAST"], "기술1번")
        self.assertEqual(data.move_names["SPECIAL_BEAM_FIRE"], "특수광선")  # typed variant
        self.assertEqual(data.move_power["MOVE_12"], 12.0)
        learnset = data.learnsets["2|Normal"]
        self.assertEqual(learnset["elite_charged"], ["MOVE_13", "SPECIAL_BEAM_FIRE"])  # numeric id
        self.assertEqual((learnset["shadow"], learnset["purified"]), (["MOVE_15"], ["MOVE_14"]))

    def test_mega_stats_and_form_changes_are_read(self):
        data = synthetic_reference()
        mega = data.temp_stats[5]["MEGA"]
        self.assertEqual((mega.attack, mega.defense, mega.stamina), (250, 150, 125))
        (change,) = data.form_changes[6]  # the duplicate entry is merged
        self.assertEqual((change["target"], change["type"], change["component"], change["stats"]),
                         ("Fused", "FUSE", "테스트몬7", [200, 180, 160]))
        self.assertEqual((change["existing_moves"], change["replacement_moves"]), (["MOVE_11"], ["MOVE_20"]))
        self.assertEqual(data.temp_boosts, {"same_type": 1.3, "different_type": 1.1, "special_move": [1.0, 1.1],
                                            "types": {"5": ["FIRE", "GRASS"]}})
        restored = reference.from_json(json.loads(json.dumps(reference.to_json(data))))
        self.assertEqual(restored.temp_boosts, data.temp_boosts)
        self.assertEqual(data.mega_moves[5]["MEGA"], {
            "move_id": "VM_MOVE_TEMP_EVOLUTION_MEGA_V0005_POKEMON_MON5", "base_move": "MOVE_12",
            "name": "기술12번+", "power": 140.0})

    def test_battle_data_is_read(self):
        data = synthetic_reference()
        self.assertEqual(len(data.type_chart), 18)
        self.assertEqual(data.weather["CLEAR"], ["FIRE"])
        self.assertEqual(data.battle["swapDurationMs"], 1000)
        self.assertEqual(data.move_stats["MOVE_11"], {"type": "NORMAL", "power": 11.0, "energy": -50,
                                                      "duration_ms": 1000, "damage_start_ms": 500})

    def test_max_moves_and_mappings_are_read(self):
        data = synthetic_reference()
        self.assertEqual(data.max_moves["VN_BM_001"], {"type": "FIRE", "kind": "attack", "name": "다이번",
                                                       "vfx": "max_flare"})
        self.assertEqual((data.max_moves["VN_BM_019"]["kind"], data.max_moves["VN_BM_019"]["name"]), ("gmax", None))
        self.assertEqual(data.max_moves["VN_BM_052"]["name"], "다이월")
        self.assertEqual(data.max_moves["VN_BM_053"]["kind"], "spirit")
        self.assertEqual(data.max_move_by_type, {"FIRE": "VN_BM_001"})
        self.assertEqual(data.species_max_moves, {"3|*": "VN_BM_019", "4|Normal": "VN_BM_019"})
        restored = reference.from_json(json.loads(json.dumps(reference.to_json(data))))
        self.assertEqual(restored.species_max_moves, data.species_max_moves)

    def test_half_levels_follow_the_game_rules(self):
        low, high = WHOLE[0], WHOLE[1]
        self.assertAlmostEqual(CPM[1.5], math.sqrt((low ** 2 + high ** 2) / 2))
        low, high = WHOLE[39], WHOLE[40]
        self.assertAlmostEqual(CPM[40.5], (low + high) / 2)
        self.assertEqual(max(CPM), 55.0)

    def test_parse_rejects_incomplete_or_inconsistent_data(self):
        with self.assertRaises(ValueError):
            reference.parse(synthetic_files(count=5))
        with self.assertRaises(ValueError):
            reference.parse(synthetic_files(whole=WHOLE[:30]))
        with self.assertRaises(ValueError):
            reference.parse(synthetic_files(check={level: 0.5 for level in range(1, 41)}))
        broken = synthetic_files()
        broken["game_master.json"] = b'[{"templateId": "PLAYER_LEVEL_SETTINGS"}]'
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
            self.assertEqual(len(manifest["files"]["game_master.json"]["sha256"]), 64)
            loaded = reference.load(data_dir)
            self.assertEqual(loaded.cpm, CPM)
            self.assertEqual(loaded.forms("테스트몬2"), synthetic_reference().forms("테스트몬2"))
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

    def test_levels_above_45_and_up_to_50_are_checked(self):
        for at_level in (45.5, 47.5, 50.0):
            with self.subTest(level=at_level):
                cp, hp = self.observed(at_level)
                result = level.estimate(self.data, "테스트몬40", IV, cp, hp)
                self.assertEqual(result["levels"], [at_level])
        self.assertEqual(result["max_level_checked"], 50.0)

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

    def test_form_display_names_follow_the_korean_client(self):
        from boxscan.forms import display_name
        self.assertIsNone(display_name(1, "Normal"))
        self.assertEqual(display_name(103, "Alola"), "알로라의 모습")
        self.assertEqual(display_name(128, "Paldea_Combat"), "팔데아의 모습 (Combat)")
        self.assertEqual(display_name(150, "A"), "아머드")
        self.assertEqual(display_name(1, "Fall_2019"), "Fall_2019")  # unknown codes stay as data

    def test_other_form_is_shown_when_only_it_matches(self):
        other = self.data.forms("테스트몬2")[1]
        multiplier = CPM[20.0]
        cp, hp = level.cp(other, IV, multiplier), level.hp(other, IV, multiplier)
        result = level.estimate(self.data, "테스트몬2", IV, cp, hp)
        self.assertEqual(result["form"], "Other")
        self.assertEqual(result["levels"], [20.0])

    def test_mega_level_uses_mega_stats_and_reports_regular_values(self):
        mega = self.data.temp_stats[5]["MEGA"]
        regular = self.data.forms("테스트몬5")[0]
        multiplier = CPM[22.5]
        result = level.estimate(self.data, "테스트몬5", IV, level.cp(mega, IV, multiplier),
                                level.hp(mega, IV, multiplier), "MEGA")
        self.assertEqual((result["status"], result["levels"]), (level.RESOLVED, [22.5]))
        self.assertEqual(result["regular"], [{"level": 22.5, "cp": level.cp(regular, IV, multiplier),
                                              "max_hp": level.hp(regular, IV, multiplier)}])
        no_mega = level.estimate(self.data, "테스트몬1", IV, 500, None, "MEGA")
        self.assertEqual(no_mega["status"], level.UNKNOWN_SPECIES)

    def test_forced_fused_form_uses_its_own_stats(self):
        from boxscan.reference import BaseStats
        fused = BaseStats(6, "Mon6", "Fused", 200, 180, 160)
        multiplier = CPM[30.0]
        result = level.estimate(self.data, "테스트몬6", IV, level.cp(fused, IV, multiplier),
                                level.hp(fused, IV, multiplier), form="Fused")
        self.assertEqual((result["levels"], result["form"]), ([30.0], "Fused"))

    def test_missing_inputs_and_reference(self):
        self.assertEqual(level.estimate(None, "테스트몬1", IV, 100, 50)["status"], level.NO_REFERENCE)
        missing = level.estimate(self.data, None, None, 100, None)
        self.assertEqual(missing["status"], level.MISSING_INPUT)
        self.assertEqual(missing["missing"], ["species", "iv"])
        self.assertEqual(level.estimate(self.data, "없는몬", IV, 100, 50)["status"], level.UNKNOWN_SPECIES)

import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import ocr
from boxscan.fields import group_frames, nickname_code, parse_frame

FFMPEG = shutil.which(os.environ.get("BOXSCAN_FFMPEG", "ffmpeg"))


def lines(*texts):
    return [{"text": text, "confidence": 1.0, "box": [0, i * 10, 10, 10]} for i, text in enumerate(texts)]


def frame(number, *texts):
    return {"frame": number, "seconds": number - 1, "duration": 1, **parse_frame(lines(*texts))}


class FieldParserTests(unittest.TestCase):
    def test_detail_values_and_noise(self):
        found = parse_frame(lines("mll 5G", "cP1234", "Ⓡ별명테스트", "99 / 120 HP", "강화"))
        self.assertEqual(found["screen"], "DETAIL")
        self.assertEqual(found["cp"], 1234)
        self.assertEqual(found["hp"], {"current": 99, "max": 120})
        self.assertNotIn("species", found)

    def test_cropped_cp_and_impossible_hp_are_not_values(self):
        found = parse_frame(lines("CP3", "130 / 120 HP"))
        self.assertNotIn("cp", found)
        self.assertNotIn("hp", found)

    def test_appraisal_caption_gives_species_not_nickname(self):
        found = parse_frame(lines("Ⓡ별명테스트", "공격", "방어", "HP",
                                  "이 테스트몬(을)를 잡은 날과 장소는 2020-01-"))
        self.assertEqual(found["screen"], "APPRAISAL")
        self.assertEqual(found["species"], "테스트몬")

    def test_lucky_label_needs_repeated_evidence(self):
        self.assertTrue(parse_frame(lines("Ⓡ별명", "반짝반짝 포컷몬", "CP1000"))["lucky"])
        lucky = [frame(n, "CP1000", "반짝반짝 포켓몬") for n in (1, 2)]
        plain = [frame(n, "CP1000", "10 / 10 HP") for n in (1, 2, 3)]
        self.assertTrue(group_frames(lucky)[0]["lucky"]["value"])
        self.assertIs(group_frames(plain)[0]["lucky"]["value"], False)
        self.assertIsNone(group_frames(plain[:1])[0]["lucky"]["value"])  # too little to say no

    def test_groups_split_on_confident_changes_only(self):
        frames = [
            frame(1, "CP1000", "100 / 100 HP"),
            frame(2, "CP1000", "100 / 100 HP"),
            frame(3, "CP1600", "100 / 100 HP"),  # one misread CP does not split
            frame(4, "이 하나몬(을)를 잡은 날과 장소는"),
            frame(5, "CP2000", "150 / 150 HP"),
            frame(6, "CP2000", "150 / 150 HP"),
            frame(7, "이 두나몬(을)를 잡은 날과 장소는"),
        ]
        first, second = group_frames(frames)
        self.assertEqual(first["frames"], [1, 2, 3, 4])
        self.assertEqual(first["cp"], {"value": 1000, "votes": 2, "observations": 3})
        self.assertEqual(first["species"]["value"], "하나몬")
        self.assertEqual(second["frames"], [5, 6, 7])
        self.assertEqual(second["max_hp"]["value"], 150)
        self.assertEqual((second["start_seconds"], second["end_seconds"]), (4, 7))

    def test_management_code_and_nickname(self):
        detail = boxed(("CP3818", 122, 44, 42, 19), ("2019", 290, 261, 31, 11),
                       ("R마기PK0042", 117, 295, 98, 20), ("반짝반짝 포컷몬", 129, 321, 72, 14),
                       ("188 / 188 HP", 137, 351, 56, 10), ("바위 / 악", 155, 451, 38, 10))
        found = parse_frame(detail)
        # The earlier "PK" form is still read; leading zeros name the same number.
        self.assertEqual((found["nickname"], found["code"]), ("R마기PK0042", "42"))
        self.assertEqual(parse_frame(lines("pk 3"))["code"], "03")
        for text in ("APK2", "PKO4", "PK0", "PK12345", "PK2 PK3"):  # no guessing, no ambiguity
            self.assertNotIn("code", parse_frame(lines(text)), text)

    def test_nickname_ending_in_a_number_is_the_code(self):
        def read(name):
            detail = boxed(("CP3492", 122, 44, 42, 19), (name, 117, 295, 98, 20), ("167 / 167 HP", 137, 351, 56, 10))
            return parse_frame(detail).get("code")
        self.assertEqual([read("R망나뇽01"), read("망나뇽 001"), read("망나뇽12"), read("망나뇽100")], ["01", "01", "12", "100"])
        for name in ("망나뇽", "망나뇽O1", "망나뇽1", "망나뇽1234"):  # no number, OCR guess, one digit, too long
            self.assertIsNone(read(name), name)
        self.assertEqual(nickname_code("R폴리곤201", "폴리곤2"), 1)  # what follows the species name
        self.assertIsNone(nickname_code("폴리곤2", "폴리곤2"))
        self.assertIsNone(nickname_code("마기01", "마기라스"))  # does not start with the species name
        self.assertNotIn("nickname", parse_frame(lines("CP1000")))

    def test_cp_candidates_keep_other_readings_of_the_cp_line(self):
        # 720 px wide: "CP34921" (extra digit), the number alone big and centred, and noise.
        found = parse_frame(boxed(("CP34921", 239, 90, 217, 48), ("17:15", 60, 33, 113, 29)), 720)
        self.assertNotIn("cp", found)
        self.assertEqual(found["cp_candidates"], [3492])
        alone = parse_frame(boxed(("3492", 287, 88, 142, 50), ("80", 618, 35, 56, 25), ("2018", 290, 600, 60, 20)), 720)
        self.assertEqual(alone["cp_candidates"], [3492])  # not the small battery or the date lower down
        self.assertEqual(parse_frame(boxed(("cP349", 241, 89, 150, 48)), 720)["cp_candidates"], [349])
        group = group_frames([{**frame(1, "cP349"), "cp_candidates": [349]}, {**frame(2, "cP349"), "cp_candidates": [349]},
                              {"frame": 3, "seconds": 2, "duration": 1, "cp_candidates": [3492]}])
        self.assertEqual(group[0]["cp_candidates"], [349, 3492])

    def test_max_moves_cut_short_in_some_frames_merge(self):
        from boxscan.fields import _max_moves
        frames = [{"max_moves": [{"name": "다이웜", "level": 2, "locked": False}, {"name": "다이리크", "level": None, "locked": True}]},
                  {"max_moves": [{"name": "다이웜", "level": 2, "locked": False}, {"name": "다이월", "level": 1, "locked": False},
                                 {"name": "다이리커버", "level": None, "locked": True}]},
                  {"max_moves": [{"name": "다이리커버리", "level": None, "locked": True}]}]
        self.assertEqual([m["name"] for m in _max_moves(frames)], ["다이웜", "다이월", "다이리커버리"])

    def test_a_different_code_starts_a_new_individual(self):
        frames = [frame(n, "CP1000", code) for n, code in ((1, "PK1"), (2, "PK1"), (3, "PK2"), (4, "PK2"))]
        first, second = group_frames(frames)
        self.assertEqual((first["frames"], first["code"]["value"]), ([1, 2], "01"))
        self.assertEqual((second["frames"], second["code"]["value"]), ([3, 4], "02"))


def boxed(*items):
    """OCR lines with explicit boxes: (text, x, y, w, h). Layout mimics a 332 px wide frame."""
    return [{"text": t, "confidence": 1.0, "box": [x, y, w, h]} for t, x, y, w, h in items]


MOVE_SCREEN = boxed(
    ("체육관 배틀 & 레이드배틀", 63, 400, 94, 11), ("트레이너 배틀", 193, 400, 52, 12),
    ("테스트펀치", 43, 435, 50, 16), ("12", 290, 434, 16, 14),
    ("• 합성빔", 46, 463, 60, 16), ("90+18", 260, 464, 46, 14),
    ("기술 날씨 보너스", 67, 486, 66, 11),
    ("해방", 77, 505, 24, 11), ("100,000", 165, 503, 60, 16), ("100", 246, 505, 25, 11),
)


class MoveParserTests(unittest.TestCase):
    def test_reads_fast_and_charged_rows_with_weather_boost(self):
        found = parse_frame(MOVE_SCREEN, 332)
        self.assertEqual(found["screen"], "DETAIL_MOVES")
        self.assertEqual(found["moves"], [
            {"name": "테스트펀치", "power": 12, "boost": None, "boost_kind": None, "role": "fast"},
            {"name": "합성빔", "power": 90, "boost": 18, "boost_kind": "weather", "role": "charged"},
        ])

    def test_header_words_in_other_text_do_not_move_the_tab_header(self):
        # A Super Mega Raid note above the moves contains "레이드" too; the real tab header is lower.
        lines = boxed(("슈퍼 메가 레이드에서 잡은 테스트몬(은)는 최소", 120, 280, 160, 11)) + MOVE_SCREEN
        self.assertEqual([m["role"] for m in parse_frame(lines, 332)["moves"]], ["fast", "charged"])
        below = MOVE_SCREEN + boxed(("레이드에서 잡은 날", 60, 560, 80, 11))  # below the moves: ignored
        self.assertEqual([m["role"] for m in parse_frame(below, 332)["moves"]], ["fast", "charged"])

    def test_missing_fast_row_does_not_promote_the_charged_move(self):
        lines = [line for line in MOVE_SCREEN if line["text"] not in ("테스트펀치", "12")]
        self.assertEqual([m["role"] for m in parse_frame(lines, 332)["moves"]], ["charged"])

    def test_scrolled_screen_without_header_marks_role_unknown(self):
        lines = [line for line in MOVE_SCREEN if "배틀" not in line["text"]]
        self.assertEqual({m["role"] for m in parse_frame(lines, 332)["moves"]}, {"unknown"})

    def test_group_votes_moves_across_frames(self):
        full = {"frame": 1, "seconds": 0, "duration": 1, "cp": 1000, **parse_frame(MOVE_SCREEN, 332)}
        partial = [line for line in MOVE_SCREEN if line["text"] not in ("테스트펀치", "12")]
        frames = [full, {**full, "frame": 2, **parse_frame(partial, 332)},
                  {**full, "frame": 3, "moves": [{"name": "합성빔", "power": 90,
                                                  "boost": 18, "boost_kind": "weather", "role": "unknown"}]}]
        (group,) = group_frames(frames)
        moves = group["moves"]
        self.assertEqual((moves["fast"]["value"], moves["fast"]["votes"]), ("테스트펀치", 1))
        self.assertEqual([(m["value"], m["votes"], m["observations"], m["power"], m["boost"])
                          for m in moves["charged"]], [("합성빔", 3, 3, 90, 18)])


class SpecialStateParserTests(unittest.TestCase):
    def test_purify_button_is_a_shadow_hint(self):
        self.assertTrue(parse_frame(lines("CP1000", "정화", "강화"))["purify_button"])
        self.assertNotIn("purify_button", parse_frame(lines("CP1000", "정화된 포켓몬", "강화")))

    def test_kyurem_fusion_buttons_are_not_moves(self):
        lines = list(MOVE_SCREEN) + boxed(("합체", 63, 300, 24, 12), ("30", 290, 300, 16, 12))
        self.assertNotIn("합체", [m["name"] for m in parse_frame(lines, 332)["moves"]])

    def test_mega_bonus_is_told_apart_from_weather(self):
        lines = boxed(("체육관 배틀 & 레이드배틀", 63, 270, 94, 11),
                      ("벌레먹기", 45, 305, 52, 19), ("5", 297, 305, 9, 14),
                      ("시저크로스", 47, 335, 62, 15), ("45", 287, 335, 19, 14),
                      ("마지막일침+", 43, 363, 76, 18), ("140+14", 255, 365, 52, 12),
                      ("메가진화 보너스", 72, 389, 59, 10))
        moves = parse_frame(lines, 332)["moves"]
        self.assertEqual([(m["name"], m["role"], m["boost"], m["boost_kind"]) for m in moves], [
            ("벌레먹기", "fast", None, None), ("시저크로스", "charged", None, None),
            ("마지막일침", "mega_plus", 14, "mega")])
        (group,) = group_frames([{"frame": 1, "seconds": 0, "duration": 1, "cp": 700, "moves": moves}])
        self.assertEqual([m["value"] for m in group["moves"]["charged"]], ["시저크로스"])
        self.assertEqual((group["moves"]["mega_plus"]["value"], group["moves"]["mega_plus"]["boost"]), ("마지막일침", 14))

    def test_max_state_moves_background_and_mega_timer(self):
        lines = boxed(("CP1714", 120, 10, 60, 20), ("드래곤 / 강철", 140, 200, 60, 12),
                      ("거다이맥스", 150, 225, 50, 12), ("남은 시간:", 150, 245, 40, 10),
                      ("맥스기술", 143, 303, 44, 12),
                      ("LOCKED", 145, 391, 40, 10), ("LOCKED", 234, 391, 39, 9), ("레벨 1", 63, 392, 27, 9),
                      ("거다이감쇠", 47, 419, 58, 16), ("다이월", 150, 419, 34, 14), ("다이리커버리", 217, 419, 70, 16),
                      ("이 포켓몬에 있는 배경은 이 포켓몬을 파트너 포켓몬으로", 40, 500, 250, 12))
        found = parse_frame(lines, 332)
        self.assertEqual((found["max_state"], found["background"], found["mega_timer"]), ("gigantamax", True, True))
        self.assertEqual(found["max_moves"], [
            {"name": "거다이감쇠", "level": 1, "locked": False},
            {"name": "다이월", "level": None, "locked": True},
            {"name": "다이리커버리", "level": None, "locked": True}])
        dynamax = parse_frame(boxed(("CP1000", 120, 10, 60, 20), ("다이맥스", 150, 225, 50, 12)), 332)
        self.assertEqual(dynamax["max_state"], "dynamax")

    def test_second_charged_move_seen_once_is_kept_when_on_screen_with_the_first(self):
        first = {"frame": 1, "seconds": 0, "duration": 1, "cp": 100, "moves": [
            {"name": "가", "power": 5, "boost": None, "boost_kind": None, "role": "fast"},
            {"name": "나", "power": 45, "boost": None, "boost_kind": None, "role": "charged"}]}
        both = {**first, "frame": 2, "moves": first["moves"] + [
            {"name": "다", "power": 140, "boost": 14, "boost_kind": "mega", "role": "charged"}]}
        frames = [first, {**first, "frame": 3}, {**first, "frame": 4}, both]
        (group,) = group_frames(frames)
        self.assertEqual([m["value"] for m in group["moves"]["charged"]], ["나", "다"])
        self.assertEqual(group["moves"]["charged"][1]["boost_kind"], "mega")


@unittest.skipUnless(ocr.swiftc() and FFMPEG, "Apple Vision OCR needs macOS with swiftc and FFmpeg")
class AppleVisionTests(unittest.TestCase):
    def test_tool_builds_and_reports_each_image(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "blank.png"
            subprocess.run(
                [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=64x64",
                 "-frames:v", "1", str(image)],
                check=True, timeout=20,
            )
            tool = ocr.build_tool(folder / "tooling")
            result = ocr.recognize([image, folder / "missing.png"], tool)
        self.assertEqual(result[image]["width"], 64)
        self.assertEqual(result[image]["lines"], [])
        self.assertEqual(result[folder / "missing.png"]["error"], "unreadable image")

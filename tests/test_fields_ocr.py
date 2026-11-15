import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import ocr
from boxscan.fields import group_frames, parse_frame

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

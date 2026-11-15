import io
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import ivbars
from boxscan.ivbars import Frame, find_windows, read_bars, read_ppm_stream

FFMPEG = shutil.which(os.environ.get("BOXSCAN_FFMPEG", "ffmpeg"))
WIDTH, HEIGHT = ivbars.REFERENCE_WIDTH, 216
WHITE, TRACK = (252, 252, 252), (218, 218, 218)
RED, ORANGE = (207, 102, 118), (229, 152, 66)
# Synthetic layout modelled on the calibration: three segments split by 1 px dividers.
LEFT, RIGHT, ROWS = 39, 155, (100, 130, 160)
SEGMENTS = ((39, 77), (78, 116), (117, 155))


def fill_end(value: int) -> int:
    if value == 0:
        return LEFT
    start, end = SEGMENTS[(value - 1) // 5]
    return round(start + ((value - 1) % 5 + 1) / 5 * (end - start))


def synthetic_frame(values: tuple[int, int, int] | None) -> Frame:
    pixels = [[WHITE] * WIDTH for _ in range(HEIGHT)]
    if values is not None:
        for row, value in zip(ROWS, values):
            colour = RED if value == 15 else ORANGE
            end = fill_end(value)
            for y in range(row - 3, row + 3):
                for x in range(LEFT, RIGHT):
                    divider = x in (SEGMENTS[0][1], SEGMENTS[1][1])
                    if divider:
                        pixels[y][x] = (255, 225, 200) if x < end else (240, 240, 240)
                    else:
                        pixels[y][x] = colour if x < end else TRACK
    return Frame(WIDTH, HEIGHT, bytes(channel for row in pixels for pixel in row for channel in pixel))


def to_ppm(frame: Frame) -> bytes:
    return f"P6\n{frame.width} {frame.height}\n255\n".encode() + frame.rgb


class IvBarTests(unittest.TestCase):
    def test_reads_every_reference_position(self):
        for value in range(16):
            with self.subTest(value=value):
                bars = read_bars(synthetic_frame((value, 15 - value, 7)))
                self.assertEqual([bar["iv"] for bar in bars], [value, 15 - value, 7])

    def test_frame_without_bars_is_not_an_appraisal(self):
        self.assertIsNone(read_bars(synthetic_frame(None)))

    def test_waits_for_a_stable_end_instead_of_a_short_pause(self):
        fps = 24
        frames = [synthetic_frame(None)] * 5
        frames += [synthetic_frame((v, v, v)) for v in range(0, 8)]
        frames += [synthetic_frame((7, 7, 7))] * 4  # a short pause in the middle of the animation
        frames += [synthetic_frame((v, v, min(v, 12))) for v in range(8, 15)]
        frames += [synthetic_frame((14, 14, 12))] * fps  # one second at the final value
        frames += [synthetic_frame(None)] * 5
        windows = find_windows(frames, fps)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["iv"], {"attack": 14, "defense": 14, "stamina": 12})
        self.assertGreaterEqual(windows[0]["stable_seconds"], 1)

    def test_closing_too_early_leaves_the_value_empty_and_resets_per_window(self):
        fps = 24
        frames = [synthetic_frame((v, v, v)) for v in range(0, 11)]
        frames += [synthetic_frame((10, 10, 10))] * 3  # closed after about 0.1 s
        frames += [synthetic_frame(None)] * fps
        frames += [synthetic_frame((5, 0, 15))] * fps  # the next individual
        first, second = find_windows(frames, fps)
        self.assertIsNone(first["iv"])
        self.assertIsNone(first["evidence_seconds"])
        self.assertEqual(second["iv"], {"attack": 5, "defense": 0, "stamina": 15})

    def test_ppm_stream_parser_reads_consecutive_images(self):
        frames = [synthetic_frame((1, 2, 3)), synthetic_frame(None)]
        parsed = list(read_ppm_stream(io.BytesIO(b"".join(map(to_ppm, frames)))))
        self.assertEqual([frame.rgb for frame in parsed], [frame.rgb for frame in frames])
        with self.assertRaises(ValueError):
            list(read_ppm_stream(io.BytesIO(to_ppm(frames[0])[:-10])))

    @unittest.skipUnless(FFMPEG, "FFmpeg not installed")
    def test_reads_an_encoded_video_through_ffmpeg(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            canvas = 720
            top = int(canvas * ivbars.SEARCH_TOP)
            sequence = [None] * 6 + [(v, v, v) for v in range(0, 12)] + [(12, 6, 15)] * 24 + [None] * 6
            for index, values in enumerate(sequence):
                band = synthetic_frame(values)
                image = bytearray(bytes(WHITE) * WIDTH * canvas)
                image[top * WIDTH * 3:(top + HEIGHT) * WIDTH * 3] = band.rgb
                (folder / f"f-{index:03d}.ppm").write_bytes(to_ppm(Frame(WIDTH, canvas, bytes(image))))
            video = folder / "appraisal.mov"
            subprocess.run(
                [FFMPEG, "-v", "error", "-framerate", "24", "-i", str(folder / "f-%03d.ppm"),
                 "-c:v", "mpeg4", "-q:v", "2", str(video)],
                check=True, timeout=60,
            )
            windows = ivbars.read_video(FFMPEG, video, 10)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["iv"], {"attack": 12, "defense": 6, "stamina": 15})

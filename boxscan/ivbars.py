"""Read appraisal IV bars from dense video frames (spec v1.2 section 5.2).

Frames are scaled to REFERENCE_WIDTH so pixel thresholds stay comparable between recordings.
The geometry below was calibrated on one iPhone recording in Korean. Check new devices and
resolutions against original frames before relying on the result.
"""
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REFERENCE_WIDTH = 332
FPS = 24
SEARCH_TOP, SEARCH_BOTTOM = 0.62, 0.92  # vertical band of the frame that holds the bars
BAR_LEFT = range(34, 44)  # expected first bar pixel at the reference width
BAR_RIGHT = range(150, 161)  # expected end (exclusive) of the bar
PROBES = (60, 96, 136)  # one x position inside each of the three segments
MIN_STABLE_SECONDS = 0.5  # the sample's fill animation took about 0.4 s
MAX_GAP_SECONDS = 0.5  # short detection gaps inside one open appraisal window
STATS = ("attack", "defense", "stamina")


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    rgb: bytes
    top: int = 0  # row offset of this crop inside the scaled frame

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]


def _saturation(p) -> int:
    return max(p) - min(p)


def _is_fill(p) -> bool:
    return _saturation(p) > 70 and max(p) > 150


def _is_track(p) -> bool:
    return _saturation(p) < 20 and 195 <= min(p) <= 232


def _is_white(p) -> bool:
    return min(p) >= 236


def _row_candidate(frame: Frame, y: int) -> bool:
    return all(_is_fill(p) or _is_track(p) for p in (frame.pixel(x, y) for x in PROBES))


def _bar_span(frame: Frame, y: int) -> tuple[int, int] | None:
    runs, start = [], None
    for x in range(frame.width + 1):
        dark = x < frame.width and not _is_white(frame.pixel(x, y))
        if dark and start is None:
            start = x
        elif not dark and start is not None:
            # Dividers on the empty track can be nearly white; bridge gaps of up to 2 px.
            if runs and start - runs[-1][1] <= 2:
                start = runs.pop()[0]
            runs.append((start, x))
            start = None
    # The trainer figure and screen edges form other non-white runs; keep the one in the bar slot.
    spans = [run for run in runs if run[0] in BAR_LEFT and run[1] in BAR_RIGHT]
    if len(spans) != 1:
        return None
    left, right = spans[0]
    bar = sum(1 for x in range(left, right) if _is_fill(p := frame.pixel(x, y)) or _is_track(p))
    return (left, right) if bar >= 0.8 * (right - left) else None


def read_bars(frame: Frame) -> list[dict] | None:
    """Return the three bars (attack, defense, stamina) or None when no appraisal is visible."""
    bands: list[list[tuple[int, tuple[int, int]]]] = []
    for y in range(frame.height):
        if not _row_candidate(frame, y) or (span := _bar_span(frame, y)) is None:
            continue
        if bands and y - bands[-1][-1][0] <= 1:
            bands[-1].append((y, span))
        else:
            bands.append([(y, span)])
    bands = [band for band in bands if len(band) >= 2]
    if len(bands) != 3:
        return None
    return [_read_row(frame, *band[len(band) // 2]) for band in bands]


def _read_row(frame: Frame, y: int, span: tuple[int, int]) -> dict:
    left, right = span
    third = (right - left) / 3

    def divider(near: float) -> int:
        return max(range(int(near) - 4, int(near) + 5), key=lambda x: sum(frame.pixel(x, y)))

    first, second = divider(left + third), divider(left + 2 * third)
    segments = [(left, first), (first + 1, second), (second + 1, right)]
    fill_end = left
    for x in range(left, right):
        if _is_fill(frame.pixel(x, y)):
            fill_end = x + 1
    references = [float(left)]
    for value in range(1, 16):
        start, end = segments[(value - 1) // 5]
        references.append(start + ((value - 1) % 5 + 1) / 5 * (end - start))
    iv = min(range(16), key=lambda value: abs(references[value] - fill_end))
    return {
        "iv": iv,
        "fill_end": fill_end,
        "distance": round(abs(references[iv] - fill_end), 1),
        "row": y + frame.top,
        "segments": segments,
    }


def find_windows(frames: Iterable[Frame], fps: float = FPS) -> list[dict]:
    """Group frames with visible bars into appraisal windows and pick a stable reading."""
    return group_windows(((index, bars) for index, frame in enumerate(frames)
                          if (bars := read_bars(frame)) is not None), fps)


def band(frame: Frame) -> Frame:
    """The part of a full frame (already REFERENCE_WIDTH wide) that holds the bars, cropped the way
    read_video asks FFmpeg to crop it."""
    top = int(frame.height * SEARCH_TOP)
    height = int(frame.height * (SEARCH_BOTTOM - SEARCH_TOP))
    row = frame.width * 3
    return Frame(frame.width, height, frame.rgb[top * row:(top + height) * row])


def group_windows(readings: Iterable[tuple[int, list[dict]]], fps: float = FPS) -> list[dict]:
    """Group (frame index, bars) readings into appraisal windows."""
    windows: list[list[tuple[int, list[dict]]]] = []
    for index, bars in readings:
        if windows and (index - windows[-1][-1][0]) / fps <= MAX_GAP_SECONDS:
            windows[-1].append((index, bars))
        else:
            windows.append([(index, bars)])
    return [_settle(window, fps) for window in windows]


def _settle(window: list[tuple[int, list[dict]]], fps: float) -> dict:
    best: list[tuple[int, list[dict]]] = []
    run: list[tuple[int, list[dict]]] = []
    for index, bars in window:
        values = [bar["iv"] for bar in bars]
        contiguous = run and index - run[-1][0] == 1
        if contiguous and values == [bar["iv"] for bar in run[-1][1]]:
            run.append((index, bars))
        else:
            run = [(index, bars)]
        if len(run) > len(best):
            best = list(run)
    stable_seconds = len(best) / fps
    result = {
        "start_seconds": round(window[0][0] / fps, 3),
        "end_seconds": round((window[-1][0] + 1) / fps, 3),
        "stable_seconds": round(stable_seconds, 3),
        "iv": None,
        "evidence_seconds": None,
    }
    if stable_seconds >= MIN_STABLE_SECONDS:
        index, bars = best[len(best) // 2]
        result["iv"] = {stat: bar["iv"] for stat, bar in zip(STATS, bars)}
        result["distance"] = {stat: bar["distance"] for stat, bar in zip(STATS, bars)}
        result["evidence_seconds"] = round(index / fps, 3)
    return result


def read_ppm_stream(stream) -> Iterator[Frame]:
    """Parse concatenated binary PPM (P6) images, as written by FFmpeg's image2pipe."""
    while True:
        header = []
        while len(header) < 4:
            token = bytearray()
            while True:
                char = stream.read(1)
                if not char:
                    if header or token:
                        raise ValueError("잘린 PPM 스트림입니다.")
                    return
                if char.isspace():
                    if token:
                        break
                    continue
                token += char
            header.append(bytes(token))
        if header[0] != b"P6" or header[3] != b"255":
            raise ValueError("지원하지 않는 PPM 형식입니다.")
        width, height = int(header[1]), int(header[2])
        size = width * height * 3
        rgb = stream.read(size)
        if len(rgb) != size:
            raise ValueError("잘린 PPM 스트림입니다.")
        yield Frame(width, height, rgb)


def read_video(ffmpeg: str, source: Path, max_seconds: int, fps: float = FPS) -> list[dict]:
    """Decode the source at a fixed rate, keep only the bar band, and find appraisal windows."""
    crop = f"crop=iw:trunc(ih*{SEARCH_BOTTOM - SEARCH_TOP}):0:trunc(ih*{SEARCH_TOP})"
    process = subprocess.Popen(
        [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "file", "-i", str(source),
            "-map", "0:v:0", "-an", "-sn", "-dn", "-t", str(max_seconds),
            "-vf", f"fps={fps},scale={REFERENCE_WIDTH}:-2,{crop}",
            "-f", "image2pipe", "-vcodec", "ppm", "-",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        windows = find_windows(read_ppm_stream(process.stdout), fps)
        _, stderr = process.communicate(timeout=120)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "FFmpeg 디코딩 실패")
    return windows

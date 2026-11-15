"""Experimental local analysis: OCR on review frames plus IV bars from the source video.

The result is written to analysis.json in the scan folder as candidates for review.
It does not create or change approved box entries.
"""
import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import ivbars, ocr
from .fields import group_frames, parse_frame
from .store import JobStore
from .video import MAX_VIDEO_SECONDS, SAMPLE_SECONDS

RESULT = "analysis.json"


def analyze_scan(store: JobStore, scan_id: str, ffmpeg: str, tool: Path | None = None) -> dict:
    job = store.get(scan_id)
    if job is None or job["status"] != "ready":
        raise ValueError("프레임 추출을 마친 스캔만 분석할 수 있습니다.")
    folder = store.root / scan_id
    images = store.frames(scan_id)
    recognized = ocr.recognize(images, tool)
    frames = []
    for path in images:
        number = int(path.stem.split("-")[-1])
        lines = recognized.get(path, {}).get("lines", [])
        frames.append({
            "frame": number,
            "seconds": (number - 1) * SAMPLE_SECONDS,
            "duration": SAMPLE_SECONDS,
            **parse_frame(lines),
            "ocr": lines,
        })
    # Drop stretches without any reading (for example transitions or other screens).
    individuals = [group for group in group_frames(frames)
                   if group["cp"] or group["max_hp"] or group["species"]]
    windows = ivbars.read_video(ffmpeg, folder / "source", MAX_VIDEO_SECONDS)
    for number, window in enumerate(windows, 1):
        if window["evidence_seconds"] is not None:
            window["evidence"] = _save_evidence(ffmpeg, folder, window["evidence_seconds"], number)
        target = _overlapping(individuals, window)
        if target is not None:
            target.setdefault("appraisals", []).append(window)
    result = {
        "scan_id": scan_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recognizer": {
            "ocr": ocr.recognizer_version(),
            "iv": f"ivbars/{ivbars.REFERENCE_WIDTH}px-{ivbars.FPS}fps",
        },
        "status": "candidate",
        "individuals": individuals,
        "unassigned_appraisals": [w for w in windows if not any(
            w in individual.get("appraisals", []) for individual in individuals)],
        "frames": frames,
    }
    output = folder / RESULT
    partial = output.with_suffix(".partial")
    partial.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(output)
    return result


def make_analyzer(store: JobStore, ffmpeg: str) -> tuple[Callable[[str], dict] | None, str]:
    """Prepare the worker's analyzer once. Returns (analyzer or None, message for the log)."""
    if ocr.swiftc() is None:
        return None, "Apple Vision OCR을 사용할 수 없어 프레임 추출만 실행합니다."
    try:
        tool = ocr.build_tool()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return None, f"Apple Vision 도구를 준비하지 못해 프레임 추출만 실행합니다: {error}"
    return (lambda scan_id: analyze_scan(store, scan_id, ffmpeg, tool)), "OCR·IV 분석을 함께 실행합니다."


def _overlapping(individuals: list[dict], window: dict) -> dict | None:
    def overlap(individual: dict) -> float:
        return (min(individual["end_seconds"], window["end_seconds"])
                - max(individual["start_seconds"], window["start_seconds"]))
    best = max(individuals, key=overlap, default=None)
    return best if best is not None and overlap(best) > 0 else None


def _save_evidence(ffmpeg: str, folder: Path, seconds: float, number: int) -> str:
    name = f"iv-{number:02d}.jpg"
    subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-protocol_whitelist", "file", "-ss", f"{seconds:.3f}", "-i", str(folder / "source"),
         "-frames:v", "1", "-q:v", "3", str(folder / name)],
        check=True, capture_output=True, timeout=60,
    )
    return name


def summary_lines(result: dict) -> list[str]:
    lines = []
    for number, individual in enumerate(result["individuals"], 1):
        def show(field):
            item = individual[field]
            return f"{item['value']} ({item['votes']}/{item['observations']})" if item else "-"
        ivs = [a["iv"] for a in individual.get("appraisals", []) if a["iv"]]
        iv = "/".join(str(v) for v in ivs[-1].values()) if ivs else "-"
        frames = individual["frames"]
        lines.append(
            f"{number}. 프레임 {frames[0]}-{frames[-1]} · 종 {show('species')} · CP {show('cp')} "
            f"· 최대 HP {show('max_hp')} · IV 공/방/체 {iv}"
        )
    return lines

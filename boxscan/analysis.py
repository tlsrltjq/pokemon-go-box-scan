"""Experimental local analysis: OCR on review frames plus IV bars from the source video.

The result is written to analysis.json in the scan folder as candidates for review.
It does not create or change approved box entries.
"""
import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import ivbars, level, ocr, reference, species
from . import moves as move_dictionary
from .fields import group_frames, parse_frame
from .store import JobStore
from .video import MAX_VIDEO_SECONDS, SAMPLE_SECONDS

RESULT = "analysis.json"
LIVE_FILE = "live.json"  # appraisal windows read during a live capture (no video is kept)


def analyze_scan(store: JobStore, scan_id: str, ffmpeg: str, tool: Path | None = None) -> dict:
    job = store.get(scan_id)
    if job is None or job["status"] != "ready":
        raise ValueError("프레임 추출을 마친 스캔만 분석할 수 있습니다.")
    folder = store.root / scan_id
    images = store.frames(scan_id)
    recognized = ocr.recognize(images, tool)
    frames = [frame_entry(path, recognized.get(path, {})) for path in images]
    live = folder / LIVE_FILE
    if not (folder / "source").exists() and live.is_file():
        # A live capture has no video: reuse the appraisal windows it read while capturing.
        saved = json.loads(live.read_text(encoding="utf-8"))
        windows, iv = saved["windows"], saved["recognizer"]
    else:
        windows = ivbars.read_video(ffmpeg, folder / "source", MAX_VIDEO_SECONDS)
        iv = f"ivbars/{ivbars.REFERENCE_WIDTH}px-{ivbars.FPS}fps"
        for number, window in enumerate(windows, 1):
            if window["evidence_seconds"] is not None:
                window["evidence"] = _save_evidence(ffmpeg, folder, window["evidence_seconds"], number)
    result = assemble(scan_id, frames, windows, _reference(store.root), iv)
    write_result(folder, result)
    return result


def frame_entry(path: Path, item: dict) -> dict:
    """One sampled frame (frame-NNN.jpg, one per second) with the fields parsed from its OCR lines."""
    number = int(path.stem.split("-")[-1])
    lines = item.get("lines", [])
    return {
        "frame": number,
        "seconds": (number - 1) * SAMPLE_SECONDS,
        "duration": SAMPLE_SECONDS,
        **parse_frame(lines, item.get("width")),
        "ocr": lines,
    }


def assemble(scan_id: str, frames: list[dict], windows: list[dict], data: reference.Reference | None,
             iv_recognizer: str) -> dict:
    """Group frames into individuals, attach appraisal windows, and resolve species and level."""
    # Drop stretches without any reading (for example transitions or other screens).
    individuals = [group for group in group_frames(frames)
                   if group["cp"] or group["max_hp"] or group["species"]]
    for window in windows:
        target = _overlapping(individuals, window)
        if target is not None:
            target.setdefault("appraisals", []).append(window)
    for individual in individuals:
        apply_reference(individual, data)
    return {
        "scan_id": scan_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recognizer": {
            "ocr": ocr.recognizer_version(),
            "iv": iv_recognizer,
            "reference": data.version if data else None,
        },
        "status": "candidate",
        "individuals": individuals,
        "unassigned_appraisals": [w for w in windows if not any(
            w in individual.get("appraisals", []) for individual in individuals)],
        "frames": frames,
    }


def detail_frame(individual: dict, readings: dict[int, dict]) -> int:
    """The frame the candidate card shows: the first with both CP and HP, else the first frame."""
    frames = individual["frames"]
    return next((n for n in frames if "cp" in readings.get(n, {}) and "hp" in readings.get(n, {})), frames[0])


def write_result(folder: Path, result: dict, name: str = RESULT) -> None:
    output = folder / name
    partial = output.with_suffix(".partial")
    partial.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(output)


def apply_reference(individual: dict, data: reference.Reference | None) -> None:
    """Resolve the on-screen species into base species and state, then level and move marks."""
    screen_name = (individual["species"] or {}).get("value")
    resolved = species.resolve(screen_name, data)
    if individual["species"] and resolved["species"] != screen_name:
        individual["species"] = {**individual["species"], "value": resolved["species"]}
    temp = resolved["temp_evolution"]
    if temp is None and (individual.get("mega_timer") or {}).get("value"):
        temp = species.single_mega(data, resolved["species"])  # timer seen, name not changed
    individual["state"] = {
        "screen_name": screen_name,
        "temp_evolution": temp,
        "temp_name": species.TEMP_NAMES.get(temp),
        "mega_active": bool(temp) or bool((individual.get("mega_timer") or {}).get("value")),
        "form": resolved["form"],
        "fusion": species.fusion_options(data, resolved["species"]),
    }
    ivs = [a["iv"] for a in individual.get("appraisals", []) if a["iv"]]
    individual["level"] = level.estimate(
        data, resolved["species"], ivs[-1] if ivs else None,
        (individual["cp"] or {}).get("value"), (individual["max_hp"] or {}).get("value"),
        temp, resolved["form"])
    move_dictionary.annotate(individual["moves"], data, resolved["species"],
                             resolved["form"] or individual["level"].get("form"))
    # Shadow candidate: the purify button, or the shadow-only charged attack (화풀이).
    moves = individual["moves"]
    evidence = []
    if (individual.get("purify_button") or {}).get("votes"):
        evidence.append("정화 버튼")
    if any(m and m.get("marker_kind") == "shadow" for m in [moves.get("fast"), *moves.get("charged", [])]):
        evidence.append("그림자 전용 기술")
    individual["shadow"] = {"value": True if evidence else None, "evidence": evidence}


def _reference(data_dir: Path) -> reference.Reference | None:
    try:
        return reference.load(data_dir)
    except (OSError, ValueError, KeyError) as error:
        print(f"기준 데이터를 읽지 못해 레벨 계산을 건너뜁니다: {error}", flush=True)
        return None


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
        found = individual.get("level") or {}
        levels = ", ".join(f"{value:g}" for value in found.get("levels", []))
        lines.append(
            f"{number}. 프레임 {frames[0]}-{frames[-1]} · 종 {show('species')} · CP {show('cp')} "
            f"· 최대 HP {show('max_hp')} · IV 공/방/체 {iv} · 레벨 {levels or '-'} "
            f"({found.get('status', '-')})"
        )
    return lines

import subprocess
import sys
import traceback
from collections.abc import Callable

from .store import JobStore

# Ten minutes, the same as a live capture. A screen recording saved on the iPhone while the Mac
# was off can hold a long batch; anything after this is not read and the scan says so.
MAX_VIDEO_SECONDS = 600
SAMPLE_SECONDS = 1
MAX_FRAMES = MAX_VIDEO_SECONDS // SAMPLE_SECONDS
EXTRACT_TIMEOUT_SECONDS = 600  # a 3-minute 1179×2556 HEVC recording took about 20 s on an M5
TRUNCATED = (f"영상이 {MAX_VIDEO_SECONDS // 60}분보다 길어 처음 {MAX_VIDEO_SECONDS // 60}분만 읽었습니다. "
             "나머지는 영상을 나눠서 다시 올려 주세요.")


Analyzer = Callable[[str], object]
SKIPPED = "이 Mac에서 Apple Vision OCR을 실행할 수 없어 분석을 건너뛰었습니다. doctor 명령으로 swiftc를 확인하세요."


def run_next(store: JobStore, ffmpeg: str, analyzer: Analyzer | None = None) -> bool:
    """Extract review frames for the next queued scan, then analyze them if possible.

    The scan becomes ready as soon as the frames exist, so the browser can show them while
    the slower OCR and IV analysis continues. An analysis failure never hides the frames.
    """
    scan_id = store.claim()
    if scan_id is None:
        return False

    folder = store.root / scan_id
    notice = None
    try:
        subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-protocol_whitelist", "file", "-threads", "2",
                "-f", "mov", "-i", str(folder / "source"),
                "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
                # One frame past the limit tells a longer video apart from one that fits.
                "-t", str(MAX_VIDEO_SECONDS + SAMPLE_SECONDS),
                "-vf", (
                    f"fps=1/{SAMPLE_SECONDS}:start_time=0:round=up,"
                    "scale=720:720:force_original_aspect_ratio=decrease:force_divisible_by=2"
                ),
                "-frames:v", str(MAX_FRAMES + 1), "-q:v", "3",
                str(folder / "frame-%03d.jpg"),
            ],
            capture_output=True,
            check=True,
            timeout=EXTRACT_TIMEOUT_SECONDS,
        )
        frames = store.frames(scan_id)
        error = None if frames else "영상에서 프레임을 찾지 못했습니다."
        if len(frames) > MAX_FRAMES:
            for path in frames[MAX_FRAMES:]:
                path.unlink(missing_ok=True)
            notice = TRUNCATED
    except subprocess.TimeoutExpired:
        error = f"영상 처리 제한 시간({EXTRACT_TIMEOUT_SECONDS}초)을 초과했습니다."
    except subprocess.CalledProcessError:
        error = "영상을 읽을 수 없습니다. 재생 가능한 MOV 또는 MP4를 선택하세요."
    except OSError:
        error = "FFmpeg를 실행할 수 없습니다. 설치와 실행 권한을 확인하세요."
    if error:
        for path in store.frames(scan_id):
            path.unlink(missing_ok=True)
    store.finish(scan_id, error, notice)
    if not error:
        record_analysis(store, scan_id, analyzer)
    return True


def record_analysis(store: JobStore, scan_id: str, analyzer: Analyzer | None) -> None:
    if analyzer is None:
        store.set_analysis(scan_id, "skipped", SKIPPED)
        return
    store.set_analysis(scan_id, "running")
    try:
        analyzer(scan_id)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        store.set_analysis(scan_id, "failed", "분석 중 오류가 발생했습니다. 워커 로그를 확인하세요.")
    else:
        store.set_analysis(scan_id, "done")
        from . import autosave  # here: autosave needs analysis, which imports this module

        autosave.after_analysis(store, scan_id)

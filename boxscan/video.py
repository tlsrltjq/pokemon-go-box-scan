import subprocess
from pathlib import Path

from .store import JobStore

MAX_VIDEO_SECONDS = 72
SAMPLE_SECONDS = 1
MAX_FRAMES = MAX_VIDEO_SECONDS // SAMPLE_SECONDS


def run_next(store: JobStore, ffmpeg: str) -> bool:
    scan_id = store.claim()
    if scan_id is None:
        return False

    folder = store.root / scan_id
    try:
        subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-protocol_whitelist", "file", "-threads", "2",
                "-f", "mov", "-i", str(folder / "source"),
                "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
                "-t", str(MAX_VIDEO_SECONDS),
                "-vf", (
                    f"fps=1/{SAMPLE_SECONDS}:start_time=0:round=up,"
                    "scale=720:720:force_original_aspect_ratio=decrease:force_divisible_by=2"
                ),
                "-frames:v", str(MAX_FRAMES), "-q:v", "3",
                str(folder / "frame-%03d.jpg"),
            ],
            capture_output=True,
            check=True,
            timeout=120,
        )
        error = None if store.frames(scan_id) else "영상에서 프레임을 찾지 못했습니다."
    except subprocess.TimeoutExpired:
        error = "영상 처리 제한 시간(120초)을 초과했습니다."
    except subprocess.CalledProcessError:
        error = "영상을 읽을 수 없습니다. 재생 가능한 MOV 또는 MP4를 선택하세요."
    except OSError:
        error = "FFmpeg를 실행할 수 없습니다. 설치와 실행 권한을 확인하세요."
    if error:
        for path in store.frames(scan_id):
            path.unlink(missing_ok=True)
    store.finish(scan_id, error)
    return True

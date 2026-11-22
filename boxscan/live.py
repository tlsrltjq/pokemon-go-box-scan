"""Live capture: read a mirrored iPhone screen (iPhone Mirroring or AirPlay) while the user browses.

The Swift helper (window_capture.swift) captures one window on this Mac. It streams small frames
at FPS for the IV bars and saves one 720 px JPEG per second for OCR, the same sampling as an
uploaded video. A live capture is an ordinary scan: its frames and analysis.json use the scan
folder and the scan page shows the candidates while the capture runs. Nothing is sent off the Mac,
the game is never touched, and no video is kept.
"""
import copy
import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from . import analysis, ivbars, ocr
from .store import JobStore

FPS = 10
MAX_SECONDS = 600
MAX_FRAMES = MAX_SECONDS  # one JPEG per second
UPDATE_SECONDS = 1.0
SOURCE = Path(__file__).with_name("window_capture.swift")
SUGGESTED_BUNDLES = ("com.apple.ScreenContinuity",)  # iPhone Mirroring
SUGGESTED_WORDS = ("iPhone", "AirPlay")
PERMISSION = ("Mac의 화면 기록 권한이 필요합니다. 시스템 설정 > 개인정보 보호 및 보안 > 화면 및 시스템 오디오 기록에서 "
              "서버를 실행한 앱(예: 터미널)을 켠 뒤 그 앱과 서버를 다시 시작하세요.")

Recognize = Callable[[list[Path]], dict[Path, dict]]


class LiveError(Exception):
    """A message for the user; the request cannot go ahead."""


def build_tool() -> Path:
    try:
        return ocr.compile_swift(SOURCE, "window_capture")
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise LiveError(f"실시간 캡처 도구를 준비하지 못했습니다: {error}") from None


def list_windows(tool: Path) -> list[dict]:
    """On-screen windows that could hold the mirrored iPhone, likely ones first."""
    completed = subprocess.run([str(tool), "list"], capture_output=True, text=True, timeout=30)
    if completed.returncode == 2:
        raise LiveError(PERMISSION)
    if completed.returncode != 0:
        raise LiveError(f"창 목록을 읽지 못했습니다: {completed.stderr.strip()}")
    windows = json.loads(completed.stdout)["windows"]
    for window in windows:
        text = f"{window['app']} {window['title']}"
        window["suggested"] = (window.get("bundle") in SUGGESTED_BUNDLES
                               or any(word.lower() in text.lower() for word in SUGGESTED_WORDS))
    return sorted(windows, key=lambda w: (not w["suggested"], w["app"], w["title"]))


def progress(individuals: list[dict]) -> dict | None:
    """Which screens of the Pokémon on screen now (the last individual) have been read, so the page
    can tell the user when to move on: detail (CP and max HP), moves (fast and a charged attack),
    appraisal (settled IV bars)."""
    if not individuals:
        return None
    individual = individuals[-1]
    moves = individual.get("moves") or {}
    has = {
        "detail": bool((individual.get("cp") or {}).get("value") and (individual.get("max_hp") or {}).get("value")),
        "moves": bool(moves.get("fast") and moves.get("charged")),
        "appraisal": any(a.get("iv") for a in individual.get("appraisals", [])),
    }
    return {"number": len(individuals), "species": (individual.get("species") or {}).get("value"),
            "nickname": (individual.get("nickname") or {}).get("value"),
            "cp": (individual.get("cp") or {}).get("value"), "start_seconds": individual.get("start_seconds"),
            "has": has, "complete": all(has.values())}


class Session:
    """One running capture. `command` must write PPM frames (FPS per second, REFERENCE_WIDTH wide)
    to stdout and frame-NNN.jpg files (one per second) into the scan folder."""

    def __init__(self, store: JobStore, scan_id: str, command: list[str], recognize: Recognize,
                 fps: int = FPS, update_seconds: float = UPDATE_SECONDS):
        self.store, self.scan_id, self.command = store, scan_id, command
        self.folder = store.root / scan_id
        self.recognize, self.fps, self.update_seconds = recognize, fps, update_seconds
        self.ticks = 0
        self.readings: list[tuple[int, list[dict]]] = []
        self.frames: list[dict] = []
        self.evidence: dict[int, str] = {}
        self.current: dict | None = None
        self.error: str | None = None
        self.finished = threading.Event()
        self._ended = threading.Event()
        self._process: subprocess.Popen | None = None
        self._data = analysis._reference(store.root)

    def start(self) -> None:
        self._process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._update, daemon=True).start()

    def stop(self, timeout: float = 120) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
        self.finished.wait(timeout)

    def status(self) -> dict:
        return {"scan_id": self.scan_id, "running": not self.finished.is_set(),
                "seconds": round(self.ticks / self.fps, 1), "frames": len(self.frames),
                "appraisals": len(self._windows(closed_only=False)), "current": self.current, "error": self.error}

    def _read(self) -> None:
        try:
            for frame in ivbars.read_ppm_stream(self._process.stdout):
                bars = ivbars.read_bars(ivbars.band(frame))
                if bars is not None:
                    self.readings.append((self.ticks, bars))
                self.ticks += 1
        except ValueError:
            pass  # a frame cut short when the helper stopped
        finally:
            self._ended.set()

    def _update(self) -> None:
        try:
            while not self._ended.wait(self.update_seconds):
                self._refresh(closed_only=True)
            self._process.wait(timeout=30)
            stderr = self._process.stderr.read().decode(errors="replace").strip()
            self._process.stdout.close()
            self._process.stderr.close()
            self._refresh(closed_only=False)
            if not self.frames:
                self.error = stderr or "캡처한 화면이 없습니다. 미러링 창이 보이는지 확인하세요."
                self.store.set_analysis(self.scan_id, "failed", self.error)
            else:
                self.store.set_analysis(self.scan_id, "done")
        except Exception as error:  # keep the scan readable and say what happened
            self.error = f"실시간 분석 중 오류가 발생했습니다: {error}"
            self.store.set_analysis(self.scan_id, "failed", self.error)
            if self._process and self._process.poll() is None:
                self._process.kill()
        finally:
            self.finished.set()

    def _windows(self, closed_only: bool) -> list[dict]:
        windows = ivbars.group_windows(list(self.readings), self.fps)
        if closed_only:  # an appraisal still on screen counts once its bars have settled
            now = self.ticks / self.fps
            windows = [w for w in windows if w["end_seconds"] + ivbars.MAX_GAP_SECONDS < now or w["iv"] is not None]
        return windows[:99]

    def _refresh(self, closed_only: bool) -> None:
        done = {frame["frame"] for frame in self.frames}
        new = [path for path in self.store.frames(self.scan_id)
               if int(path.stem.split("-")[-1]) not in done][:MAX_FRAMES - len(self.frames)]
        if new:
            recognized = self.recognize(new)
            self.frames += [analysis.frame_entry(path, recognized.get(path, {})) for path in new]
            self.frames.sort(key=lambda frame: frame["frame"])
        windows = self._windows(closed_only)
        for number, window in enumerate(windows, 1):
            if window["evidence_seconds"] is not None:
                window["evidence"] = self._evidence(number, window["evidence_seconds"])
        recognizer = f"ivbars/{ivbars.REFERENCE_WIDTH}px-{self.fps}fps-live"
        analysis.write_result(self.folder, {"recognizer": recognizer, "fps": self.fps, "windows": windows},
                              analysis.LIVE_FILE)
        if self.frames:
            result = analysis.assemble(self.scan_id, copy.deepcopy(self.frames), copy.deepcopy(windows),
                                       self._data, recognizer)
            analysis.write_result(self.folder, result)
            self.current = progress(result["individuals"])

    def _evidence(self, number: int, seconds: float) -> str | None:
        """The saved second showing the settled bars (no video is kept for an exact frame). Seconds
        whose screen did not change are not saved, so the last saved one at or before it shows them."""
        if number not in self.evidence:
            wanted = round(seconds) + 1
            saved = sorted(int(path.stem.split("-")[-1]) for path in self.store.frames(self.scan_id))
            earlier = [n for n in saved if n <= wanted]
            if not earlier:
                return None
            source = self.folder / f"frame-{earlier[-1]:03d}.jpg"
            name = f"iv-{number:02d}.jpg"
            shutil.copyfile(source, self.folder / name)
            self.evidence[number] = name
        return self.evidence[number]


class Manager:
    """At most one live capture at a time for the server."""

    def __init__(self, store: JobStore, tool: Callable[[], Path] = build_tool,
                 recognize: Callable[[], Recognize] | None = None,
                 command: Callable[[Path, int, Path], list[str]] | None = None):
        self.store, self._tool, self._recognize = store, tool, recognize or self._vision
        self._command = command or (lambda tool, window, folder: [
            str(tool), "stream", str(window), str(folder), str(FPS), str(MAX_SECONDS), str(ivbars.REFERENCE_WIDTH)])
        self.session: Session | None = None
        self._lock = threading.Lock()
        # A capture cut off by a server restart keeps what it had read.
        for scan_id in store.with_analysis("live"):
            readable = (store.root / scan_id / analysis.RESULT).is_file()
            store.set_analysis(scan_id, "done" if readable else "failed",
                               None if readable else "서버가 종료되어 실시간 캡처가 중단되었습니다.")

    @staticmethod
    def _vision() -> Recognize:
        if ocr.swiftc() is None:
            raise LiveError("Apple Vision OCR을 쓸 수 없어 실시간 캡처를 시작할 수 없습니다. doctor 명령으로 swiftc를 확인하세요.")
        tool = ocr.build_tool()
        return lambda images: ocr.recognize(images, tool)

    def windows(self) -> list[dict]:
        return list_windows(self._tool())

    def start(self, window_id: int, label: str | None = None) -> str:
        with self._lock:
            if self.session and not self.session.finished.is_set():
                raise LiveError("이미 실시간 캡처 중입니다. 먼저 중지하세요.")
            recognize = self._recognize()
            tool = self._tool()
            scan_id = str(uuid4())
            folder = self.store.root / scan_id
            folder.mkdir()
            name = f"실시간 캡처 {datetime.now().strftime('%Y-%m-%d %H:%M')}" + (f" · {label}" if label else "")
            self.store.create(scan_id, name, status="ready")
            self.store.set_analysis(scan_id, "live")
            self.session = Session(self.store, scan_id, self._command(tool, window_id, folder), recognize)
            self.session.start()
            return scan_id

    def stop(self) -> dict | None:
        session = self.session
        if session is None:
            return None
        session.stop()
        return session.status()

    def status(self) -> dict | None:
        return self.session.status() if self.session else None

    def shutdown(self) -> None:
        if self.session and not self.session.finished.is_set():
            self.session.stop(timeout=30)


def wait_until(predicate: Callable[[], bool], timeout: float, step: float = 0.05) -> bool:
    """Small polling helper for tests and the CLI."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()

import json
import stat
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from boxscan import analysis, ivbars, live
from boxscan.store import JobStore
from test_ivbars import synthetic_frame, to_ppm

FPS = 10
HEIGHT = 720  # the IV band is rows 446-661 of a 720 px tall frame, like a phone screen at 332 px wide

FAKE_CAPTURE = textwrap.dedent("""
    import json, sys, time
    folder, plan, band_path = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
    bands = json.load(open(band_path))
    out = sys.stdout.buffer
    for second, key in enumerate(plan):
        open(f"{folder}/frame-{second + 1:03d}.jpg.partial", "wb").write(b"jpeg")
        import os; os.replace(f"{folder}/frame-{second + 1:03d}.jpg.partial", f"{folder}/frame-{second + 1:03d}.jpg")
        for _ in range(%d):
            out.write(bytes.fromhex(bands[key])); out.flush(); time.sleep(0.002)
""" % FPS)


def full_frame(values) -> bytes:
    """A whole 332 x 720 frame (PPM) with the synthetic bars in the IV band."""
    band = synthetic_frame(values)
    white = bytes([252]) * (band.width * 3)
    top = int(HEIGHT * ivbars.SEARCH_TOP)
    rows = top * [white] + [band.rgb] + (HEIGHT - top - band.height) * [white]
    rgb = b"".join(rows)
    return f"P6\n{band.width} {HEIGHT}\n255\n".encode() + rgb


def lines(*texts):
    return [{"text": text, "confidence": 1.0, "box": [0, i * 10, 10, 10]} for i, text in enumerate(texts)]


DETAIL = lines("cP1234", "120 / 120 HP")
APPRAISAL = lines("공격", "방어", "HP", "이 물몬(을)를 잡은 날과 장소는 2020-01-")


def fake_recognize(screens: dict[int, list]):
    calls = []

    def recognize(paths):
        calls.append([p.name for p in paths])
        return {p: {"lines": screens.get(int(p.stem.split("-")[-1]), []), "width": 720} for p in paths}
    recognize.calls = calls
    return recognize


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = JobStore(self.root / "data")
        script = self.root / "fake_capture.py"
        script.write_text(FAKE_CAPTURE, encoding="utf-8")
        bands = {"none": full_frame(None).hex(), "bars": full_frame((15, 14, 13)).hex()}
        (self.root / "bands.json").write_text(json.dumps(bands), encoding="utf-8")
        self.script = script

    def command(self, plan):
        return lambda tool, window, folder: [sys.executable, str(self.script), str(folder), json.dumps(plan),
                                             str(self.root / "bands.json")]


class SessionTests(Fixture):
    def test_capture_reads_bars_and_screens_into_a_scan(self):
        plan = ["none", "none", "none", "bars", "bars", "bars", "none"]
        recognize = fake_recognize({1: DETAIL, 2: DETAIL, 3: DETAIL, 4: APPRAISAL, 5: APPRAISAL, 6: APPRAISAL})
        manager = live.Manager(self.store, tool=lambda: Path("/bin/true"), recognize=lambda: recognize,
                               command=self.command(plan))
        scan_id = manager.start(7, "테스트 창")
        session = manager.session
        session.update_seconds = 0.05
        self.assertTrue(session.finished.wait(30))
        job = self.store.get(scan_id)
        self.assertEqual((job["status"], job["analysis_status"]), ("ready", "done"))
        self.assertTrue(job["filename"].startswith("실시간 캡처 ") and job["filename"].endswith("· 테스트 창"))
        status = manager.status()
        self.assertEqual((status["running"], status["frames"], status["appraisals"], status["seconds"]),
                         (False, 7, 1, 7.0))
        # The checklist for the Pokémon on screen: detail and appraisal read, no move lines shown.
        self.assertEqual(status["current"]["has"], {"detail": True, "moves": False, "appraisal": True})
        self.assertEqual((status["current"]["number"], status["current"]["species"], status["current"]["complete"]),
                         (1, "물몬", False))
        result = json.loads((self.root / "data" / scan_id / analysis.RESULT).read_text(encoding="utf-8"))
        (individual,) = result["individuals"]
        self.assertEqual((individual["cp"]["value"], individual["species"]["value"]), (1234, "물몬"))
        (window,) = individual["appraisals"]
        self.assertEqual(window["iv"], {"attack": 15, "defense": 14, "stamina": 13})
        self.assertEqual(window["start_seconds"], 3.0)  # bars from the 4th second
        self.assertTrue((self.root / "data" / scan_id / window["evidence"]).is_file())
        self.assertEqual(result["recognizer"]["iv"], "ivbars/332px-10fps-live")
        # Every frame is read once, in order, across the periodic updates.
        read = [name for call in recognize.calls for name in call]
        self.assertEqual(read, [f"frame-{n:03d}.jpg" for n in range(1, 8)])
        saved = json.loads((self.root / "data" / scan_id / analysis.LIVE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(len(saved["windows"]), 1)

        # Re-analysis of a live scan (no video) reuses the saved appraisal windows.
        with mock.patch.object(analysis.ocr, "recognize", lambda images, tool=None: recognize(images)):
            again = analysis.analyze_scan(self.store, scan_id, "ffmpeg-not-needed")
        self.assertEqual(again["individuals"][0]["appraisals"][0]["iv"], window["iv"])

    def test_stop_ends_early_and_one_capture_at_a_time(self):
        manager = live.Manager(self.store, tool=lambda: Path("/bin/true"), recognize=lambda: fake_recognize({}),
                               command=self.command(["none"] * 300))
        manager.start(1)
        manager.session.update_seconds = 0.05
        with self.assertRaises(live.LiveError):
            manager.start(2)
        live.wait_until(lambda: manager.status()["frames"] >= 1, 10)
        stopped = manager.stop()
        self.assertFalse(stopped["running"])
        self.assertLess(stopped["seconds"], 300)
        # No readings: the scan still finishes and shows no individuals.
        self.assertEqual(self.store.get(stopped["scan_id"])["analysis_status"], "done")

    def test_failed_helper_reports_its_message(self):
        manager = live.Manager(self.store, tool=lambda: Path("/bin/true"), recognize=lambda: fake_recognize({}),
                               command=lambda tool, window, folder: [
                                   sys.executable, "-c", "import sys; sys.exit('창을 찾지 못했습니다.')"])
        scan_id = manager.start(1)
        self.assertTrue(manager.session.finished.wait(30))
        job = self.store.get(scan_id)
        self.assertEqual((job["analysis_status"], job["analysis_error"]), ("failed", "창을 찾지 못했습니다."))

    def test_restart_closes_captures_left_running(self):
        for scan_id, readable in (("a", True), ("b", False)):
            (self.root / "data" / scan_id).mkdir()
            self.store.create(scan_id, "실시간", status="ready")
            self.store.set_analysis(scan_id, "live")
            if readable:
                (self.root / "data" / scan_id / analysis.RESULT).write_text("{}", encoding="utf-8")
        live.Manager(self.store, tool=lambda: Path("/bin/true"))
        self.assertEqual(self.store.get("a")["analysis_status"], "done")
        self.assertEqual(self.store.get("b")["analysis_status"], "failed")


class ProgressTests(Fixture):
    def individual(self, cp=None, hp=None, fast=None, charged=(), iv=None):
        return {"species": {"value": "물몬"}, "cp": {"value": cp} if cp else None,
                "max_hp": {"value": hp} if hp else None, "start_seconds": 3,
                "moves": {"fast": {"value": fast} if fast else None, "charged": [{"value": c} for c in charged]},
                "appraisals": [{"iv": iv}] if iv is not None else []}

    def test_checklist_follows_the_last_individual(self):
        self.assertIsNone(live.progress([]))
        done = self.individual(1234, 120, "물튀기기", ("파도",), {"attack": 15, "defense": 14, "stamina": 13})
        partial = self.individual(1000, None, "물튀기기")
        state = live.progress([done, partial])
        self.assertEqual((state["number"], state["has"], state["complete"]),
                         (2, {"detail": False, "moves": False, "appraisal": False}, False))
        self.assertTrue(live.progress([done])["complete"])
        unsettled = self.individual(1234, 120, "물튀기기", ("파도",), iv=None)
        unsettled["appraisals"] = [{"iv": None}]  # bars seen but still moving
        self.assertFalse(live.progress([unsettled])["has"]["appraisal"])

    def test_settled_bars_count_while_the_appraisal_is_still_open(self):
        (self.root / "data" / "s").mkdir()
        session = live.Session(self.store, "s", ["true"], fake_recognize({}))
        bars = ivbars.read_bars(synthetic_frame((15, 14, 13)))
        session.readings = [(tick, bars) for tick in range(10)]
        session.ticks = 10  # bars still on screen at the last tick
        (window,) = session._windows(closed_only=True)
        self.assertEqual(window["iv"], {"attack": 15, "defense": 14, "stamina": 13})
        session.readings = [(tick, bars) for tick in range(3)]  # 0.3 s: still filling
        session.ticks = 3
        self.assertEqual(session._windows(closed_only=True), [])


class WindowListTests(Fixture):
    def tool(self, body: str) -> Path:
        path = self.root / "tool.sh"
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def test_mirroring_windows_come_first(self):
        windows = [{"id": 1, "app": "Safari", "bundle": "com.apple.Safari", "title": "문서", "width": 800, "height": 600},
                   {"id": 2, "app": "iPhone 미러링", "bundle": "com.apple.ScreenContinuity", "title": "", "width": 330,
                    "height": 700}]
        tool = self.tool(f"echo '{json.dumps({'windows': windows}, ensure_ascii=False)}'")
        listed = live.list_windows(tool)
        self.assertEqual([(w["id"], w["suggested"]) for w in listed], [(2, True), (1, False)])

    def test_missing_permission_explains_the_setting(self):
        with self.assertRaises(live.LiveError) as raised:
            live.list_windows(self.tool('echo \'{"error": "permission"}\'; exit 2'))
        self.assertIn("화면 기록 권한", str(raised.exception))


class BandTests(unittest.TestCase):
    def test_band_crop_reads_the_same_bars_as_the_video_path(self):
        frame = next(ivbars.read_ppm_stream(__import__("io").BytesIO(full_frame((3, 9, 15)))))
        bars = ivbars.read_bars(ivbars.band(frame))
        self.assertEqual([b["iv"] for b in bars], [3, 9, 15])
        self.assertEqual([b["row"] for b in bars], [100, 130, 160])  # rows inside the band, as from FFmpeg


@unittest.skipUnless(sys.platform == "darwin" and __import__("boxscan.ocr").ocr.swiftc(), "macOS swiftc 필요")
class CaptureToolTests(unittest.TestCase):
    def test_tool_builds_and_lists_windows_or_asks_for_permission(self):
        import subprocess
        from boxscan import ocr
        with TemporaryDirectory() as temporary:
            tool = ocr.compile_swift(live.SOURCE, "window_capture", Path(temporary))
            if __import__("os").environ.get("CI"):
                return  # hosted runners have no screen session to ask; compiling is the check there
            completed = subprocess.run([str(tool), "list"], capture_output=True, text=True, timeout=60)
        # Without Screen Recording permission (as on CI) the helper says so instead of failing silently.
        self.assertIn(completed.returncode, (0, 2), completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue("windows" in payload if completed.returncode == 0 else payload["error"] == "permission")

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "FFmpeg 필요")
    def test_crop_finds_the_phone_inside_gray_frame_and_black_padding(self):
        # Like a mirroring window that shrank after the capture started: gray window frame and toolbar,
        # the phone screen, then black padding to the right and below.
        import subprocess
        from boxscan import ocr
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "window.png"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0xd8d8d8:s=720x1583",
                            "-vf", "drawbox=x=553:y=0:w=167:h=1583:color=black:t=fill,"
                                   "drawbox=x=0:y=1186:w=720:h=397:color=black:t=fill,"
                                   "drawbox=x=13:y=66:w=527:h=1107:color=0x3080c0:t=fill",
                            "-frames:v", "1", str(image)], check=True, timeout=30)
            tool = ocr.compile_swift(live.SOURCE, "window_capture", folder / "tooling")
            completed = subprocess.run([str(tool), "crop", str(image)], capture_output=True, text=True, timeout=60)
        rect = json.loads(completed.stdout)
        for key, expected in (("x", 13), ("y", 66), ("width", 527), ("height", 1107)):
            self.assertAlmostEqual(rect[key], expected, delta=2, msg=key)


class LiveApiTests(Fixture):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from boxscan.api import create_app
        self.recognize = fake_recognize({1: DETAIL, 2: DETAIL})
        self.manager = live.Manager(self.store, tool=lambda: Path("/bin/true"), recognize=lambda: self.recognize,
                                    command=self.command(["none"] * 200))
        self.client = TestClient(create_app(self.root / "data", live_manager=self.manager), base_url="http://localhost")
        self.addCleanup(self.client.close)

    def test_start_watch_and_stop(self):
        self.assertIsNone(self.client.get("/api/live").json()["session"])
        self.assertEqual(self.client.post("/api/live/stop").status_code, 409)
        for bad in ({}, {"window_id": "7"}, {"window_id": True}, {"window_id": 0}):
            self.assertEqual(self.client.post("/api/live/start", json=bad).status_code, 422)
        response = self.client.post("/api/live/start", json={"window_id": 7, "label": "iPhone 미러링"})
        self.assertEqual(response.status_code, 202, response.text)
        scan_id = response.json()["id"]
        self.manager.session.update_seconds = 0.05
        self.assertEqual(self.client.post("/api/live/start", json={"window_id": 7}).status_code, 409)
        self.assertTrue(live.wait_until(
            lambda: self.client.get(f"/api/scans/{scan_id}").json()["analysis"]["individuals"], 15))
        scan = self.client.get(f"/api/scans/{scan_id}").json()
        self.assertEqual(scan["analysis"]["status"], "live")  # partial result while capturing
        self.assertTrue(scan["live"]["running"])
        self.assertEqual(self.client.get(scan["frames"][0]).status_code, 200)
        stopped = self.client.post("/api/live/stop").json()
        self.assertFalse(stopped["running"])
        scan = self.client.get(f"/api/scans/{scan_id}").json()
        self.assertEqual((scan["analysis"]["status"], scan["live"]["running"]), ("done", False))
        self.assertEqual(scan["analysis"]["individuals"][0]["cp"]["value"], 1234)

    def test_permission_error_is_shown(self):
        def denied():
            raise live.LiveError(live.PERMISSION)
        self.manager._tool = denied
        response = self.client.get("/api/live/windows")
        self.assertEqual(response.status_code, 409)
        self.assertIn("화면 기록 권한", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()

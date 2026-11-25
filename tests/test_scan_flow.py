import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import struct
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fastapi.testclient import TestClient

from boxscan import backup
from boxscan.api import create_app
from boxscan.store import JobStore
from boxscan.video import run_next

FFMPEG = shutil.which(os.environ.get("BOXSCAN_FFMPEG", "ffmpeg"))


def has_encoder(name: str) -> bool:
    if not FFMPEG:
        return False
    listed = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20)
    return f" {name} " in listed.stdout


def jpeg_size(data: bytes) -> tuple[int, int]:
    """Width and height from the first start-of-frame marker."""
    index = 2
    while index < len(data):
        marker, length = data[index + 1], struct.unpack(">H", data[index + 2:index + 4])[0]
        if 0xC0 <= marker <= 0xC3:
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            return width, height
        index += 2 + length
    raise ValueError("no SOF marker")


class ScanFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = JobStore(self.root / "data")
        self.client = TestClient(create_app(self.store.root), base_url="http://localhost")
        self.addCleanup(self.client.close)

    def upload(self, filename, data, **headers):
        return self.client.post(
            "/api/scans", params={"filename": filename}, content=data,
            headers={"Content-Type": "application/octet-stream", **headers},
        )

    def test_invalid_upload_does_not_create_a_job(self):
        self.assertEqual(self.upload("empty.mov", b"").status_code, 422)
        self.assertEqual(self.upload("../escape.mp4", b"data").status_code, 422)
        self.assertEqual(self.upload("cross-origin.mp4", b"data", Origin="http://evil.test").status_code, 403)
        self.assertIsNone(self.store.claim())
        self.assertFalse(any(path.is_dir() for path in self.store.root.iterdir()))

    def test_lan_requires_password_and_rejects_other_host(self):
        with TestClient(
            create_app(self.store.root, "192.168.1.9", "test password only"),
            base_url="http://192.168.1.9:8000",
        ) as client:
            self.assertEqual(client.get("/").status_code, 401)
            self.assertEqual(client.get("/", auth=("boxscan", "test password only")).status_code, 200)
            self.assertEqual(
                client.get("/", headers={"Host": "bad.example"}, auth=("boxscan", "test password only")).status_code,
                400,
            )

    @unittest.skipUnless(FFMPEG, "FFmpeg not installed")
    def test_real_video_upload_worker_and_frame_download(self):
        sample = self.root / "sample.mp4"
        subprocess.run(
            [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=96x128:r=12",
             "-t", "4", "-c:v", "mpeg4", str(sample)],
            check=True, timeout=20,
        )
        response = self.upload("샘플.mp4", sample.read_bytes())
        self.assertEqual(response.status_code, 202)
        scan = f"/api/scans/{response.json()['id']}"
        self.assertEqual(self.client.get(scan).json()["status"], "queued")
        self.assertTrue(run_next(self.store, FFMPEG))
        result = self.client.get(scan).json()
        self.assertEqual(result["status"], "ready", result)
        self.assertEqual(len(result["frames"]), 4)
        image = self.client.get(result["frames"][-1])
        self.assertEqual(image.headers["Content-Type"], "image/jpeg")
        self.assertTrue(image.content.startswith(bytes.fromhex("ffd8")))
        self.assertEqual(result["analysis"]["status"], "skipped")
        self.assertIn("swiftc", result["analysis"]["error"])
        self.assertFalse(run_next(self.store, FFMPEG))

    def make_sample(self):
        sample = self.root / "sample.mp4"
        subprocess.run(
            [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=96x128:r=12",
             "-t", "3", "-c:v", "mpeg4", str(sample)],
            check=True, timeout=20,
        )
        return sample.read_bytes()

    @unittest.skipUnless(FFMPEG, "FFmpeg not installed")
    def test_worker_analysis_result_is_summarized_without_raw_ocr(self):
        scan_id = self.upload("샘플.mp4", self.make_sample()).json()["id"]

        def fake_analyzer(analyzed_id):
            folder = self.store.root / analyzed_id
            self.assertEqual(self.store.get(analyzed_id)["status"], "ready")
            self.assertEqual(self.store.get(analyzed_id)["analysis_status"], "running")
            (folder / "iv-01.jpg").write_bytes((folder / "frame-002.jpg").read_bytes())
            individual = {
                "frames": [1, 2, 3], "start_seconds": 0, "end_seconds": 3,
                "cp": {"value": 1234, "votes": 2, "observations": 2},
                "max_hp": {"value": 120, "votes": 1, "observations": 1},
                "species": {"value": "테스트몬", "votes": 1, "observations": 1},
                "appraisals": [{"start_seconds": 1, "end_seconds": 2.5, "stable_seconds": 1.0,
                                "iv": {"attack": 1, "defense": 2, "stamina": 3},
                                "evidence_seconds": 1.5, "evidence": "iv-01.jpg"}],
            }
            frames = [{"frame": 1, "ocr": [{"text": "합성 원문"}]},
                      {"frame": 2, "cp": 1234, "hp": {"current": 99, "max": 120}, "ocr": []}]
            (folder / "analysis.json").write_text(json.dumps({
                "individuals": [individual], "unassigned_appraisals": [], "frames": frames,
                "recognizer": {"ocr": "fake"},
            }), encoding="utf-8")

        self.assertTrue(run_next(self.store, FFMPEG, fake_analyzer))
        response = self.client.get(f"/api/scans/{scan_id}")
        analysis = response.json()["analysis"]
        self.assertEqual(analysis["status"], "done")
        (individual,) = analysis["individuals"]
        self.assertEqual(individual["species"]["value"], "테스트몬")
        self.assertEqual(individual["detail_frame"], f"/api/scans/{scan_id}/frames/2")
        self.assertEqual(individual["appraisals"][0]["iv"], {"attack": 1, "defense": 2, "stamina": 3})
        self.assertNotIn("합성 원문", response.text)
        evidence = self.client.get(individual["appraisals"][0]["evidence"])
        self.assertEqual(evidence.headers["Content-Type"], "image/jpeg")
        self.assertEqual(self.client.get(f"/api/scans/{scan_id}/evidence/2").status_code, 404)

    @unittest.skipUnless(FFMPEG, "FFmpeg not installed")
    def test_analysis_failure_keeps_frames_available(self):
        scan_id = self.upload("샘플.mp4", self.make_sample()).json()["id"]

        def broken_analyzer(_):
            raise RuntimeError("synthetic failure")

        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            self.assertTrue(run_next(self.store, FFMPEG, broken_analyzer))
        self.assertIn("synthetic failure", log.getvalue())
        result = self.client.get(f"/api/scans/{scan_id}").json()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["frames"]), 3)
        self.assertEqual(result["analysis"]["status"], "failed")
        self.assertNotIn("synthetic failure", result["analysis"]["error"])

    def test_existing_database_gains_analysis_columns(self):
        root = self.root / "old"
        root.mkdir()
        with sqlite3.connect(root / "scans.sqlite3") as db:
            db.execute("CREATE TABLE scans (id TEXT PRIMARY KEY, filename TEXT NOT NULL, "
                       "status TEXT NOT NULL, error TEXT)")
            db.execute("INSERT INTO scans VALUES ('old', 'old.mp4', 'ready', NULL)")
        db.close()
        # Server and worker often start together; every concurrent migration must succeed.
        barrier = threading.Barrier(6)
        errors = []

        def open_store():
            barrier.wait()
            try:
                JobStore(root)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=open_store) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        store = JobStore(root)
        self.assertIsNone(store.get("old")["analysis_status"])
        store.set_analysis("old", "done")
        self.assertEqual(store.get("old")["analysis_status"], "done")

    @unittest.skipUnless(FFMPEG, "FFmpeg not installed")
    def test_invalid_video_marks_job_failed(self):
        response = self.upload("broken.mov", b"not a video")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(run_next(self.store, FFMPEG))
        result = self.client.get(f"/api/scans/{response.json()['id']}").json()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["error"])
        self.assertEqual(result["frames"], [])

    @unittest.skipUnless(has_encoder("libx265"), "FFmpeg without libx265")
    def test_iphone_original_portrait_hevc_mov_is_accepted(self):
        # Same container, codec and aspect ratio as an iPhone's own screen recording (synthetic content).
        sample = self.root / "IMG_0001.MOV"
        subprocess.run(
            [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1170x2532:r=30", "-t", "3",
             "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
             "-tag:v", "hvc1", "-pix_fmt", "yuv420p", str(sample)],
            check=True, timeout=120,
        )
        response = self.upload(sample.name, sample.read_bytes())
        self.assertEqual(response.status_code, 202)
        self.assertTrue(run_next(self.store, FFMPEG))
        result = self.client.get(f"/api/scans/{response.json()['id']}").json()
        self.assertEqual(result["status"], "ready", result)
        self.assertIsNone(result["notice"])
        self.assertEqual(len(result["frames"]), 3)
        # Review frames keep the portrait shape at the size the IV and OCR rules were tuned on.
        self.assertEqual(jpeg_size(self.client.get(result["frames"][0]).content), (332, 720))

    @unittest.skipUnless(FFMPEG, "FFmpeg not installed")
    def test_video_longer_than_limit_keeps_first_part_and_says_so(self):
        long_sample = self.root / "long.mp4"
        subprocess.run(
            [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=96x128:r=12",
             "-t", "5", "-c:v", "mpeg4", str(long_sample)],
            check=True, timeout=20,
        )
        with mock.patch("boxscan.video.MAX_VIDEO_SECONDS", 3), mock.patch("boxscan.video.MAX_FRAMES", 3):
            long_id = self.upload("long.mp4", long_sample.read_bytes()).json()["id"]
            self.assertTrue(run_next(self.store, FFMPEG))
            fits_id = self.upload("fits.mp4", self.make_sample()).json()["id"]  # exactly 3 seconds
            self.assertTrue(run_next(self.store, FFMPEG))
        long_scan = self.client.get(f"/api/scans/{long_id}").json()
        self.assertEqual(long_scan["status"], "ready")
        self.assertIsNone(long_scan["error"])
        self.assertEqual(len(long_scan["frames"]), 3)
        self.assertIn("나눠서", long_scan["notice"])
        fits = self.client.get(f"/api/scans/{fits_id}").json()
        self.assertEqual(len(fits["frames"]), 3)
        self.assertIsNone(fits["notice"])

    @unittest.skipUnless(FFMPEG, "FFmpeg not installed")
    def test_more_than_99_iv_evidence_images_stay_reachable(self):
        scan_id = self.upload("샘플.mp4", self.make_sample()).json()["id"]

        def fake_analyzer(analyzed_id):
            folder = self.store.root / analyzed_id
            (folder / "iv-100.jpg").write_bytes((folder / "frame-001.jpg").read_bytes())
            window = {"start_seconds": 1, "end_seconds": 2, "stable_seconds": 1.0,
                      "iv": {"attack": 15, "defense": 15, "stamina": 15}, "evidence": "iv-100.jpg"}
            individual = {"frames": [1, 2], "start_seconds": 0, "end_seconds": 3,
                          "cp": None, "max_hp": None, "species": None, "appraisals": [window]}
            (folder / "analysis.json").write_text(json.dumps({
                "individuals": [individual], "unassigned_appraisals": [], "frames": []}), encoding="utf-8")

        self.assertTrue(run_next(self.store, FFMPEG, fake_analyzer))
        (individual,) = self.client.get(f"/api/scans/{scan_id}").json()["analysis"]["individuals"]
        link = individual["appraisals"][0]["evidence"]
        self.assertEqual(link, f"/api/scans/{scan_id}/evidence/100")
        self.assertEqual(self.client.get(link).headers["Content-Type"], "image/jpeg")
        self.assertTrue(any(pattern.match(f"scans/{scan_id}/iv-100.jpg") for pattern in backup.MEMBERS))

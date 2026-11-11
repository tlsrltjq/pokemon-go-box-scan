import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from boxscan.api import create_app
from boxscan.store import JobStore
from boxscan.video import run_next

FFMPEG = shutil.which(os.environ.get("BOXSCAN_FFMPEG", "ffmpeg"))


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
        self.assertTrue(result["frames"])
        image = self.client.get(result["frames"][0])
        self.assertEqual(image.headers["Content-Type"], "image/jpeg")
        self.assertTrue(image.content.startswith(bytes.fromhex("ffd8")))
        self.assertFalse(run_next(self.store, FFMPEG))

    @unittest.skipUnless(FFMPEG, "FFmpeg not installed")
    def test_invalid_video_marks_job_failed(self):
        response = self.upload("broken.mov", b"not a video")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(run_next(self.store, FFMPEG))
        result = self.client.get(f"/api/scans/{response.json()['id']}").json()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["error"])
        self.assertEqual(result["frames"], [])

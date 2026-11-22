import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boxscan import cleanup
from boxscan.box import BoxStore
from boxscan.store import JobStore


def make_scan(store: JobStore, scan_id: str, frames: int = 6, video: bool = True) -> Path:
    """A finished scan: frames 1-3 are one individual (detail with CP and HP on frame 2), 4-6 another."""
    folder = store.root / scan_id
    folder.mkdir()
    store.create(scan_id, f"{scan_id}.mp4")
    store.claim()
    store.finish(scan_id)
    store.set_analysis(scan_id, "done")
    if video:
        (folder / "source").write_bytes(b"v" * 5000)
    for number in range(1, frames + 1):
        (folder / f"frame-{number:03d}.jpg").write_bytes(b"f" * 1000)
    (folder / "iv-01.jpg").write_bytes(b"e" * 300)
    readings = [{"frame": 1}, {"frame": 2, "cp": 100, "hp": {"max": 50}}, {"frame": 3},
                {"frame": 4}, {"frame": 5}, {"frame": 6}]
    blank = {"species": None, "cp": None, "max_hp": None}
    result = {"individuals": [{"frames": [1, 2, 3], "start_seconds": 0, "end_seconds": 3, **blank},
                              {"frames": [4, 5, 6], "start_seconds": 3, "end_seconds": 6, **blank}],
              "frames": readings}
    (folder / "analysis.json").write_text(json.dumps(result), encoding="utf-8")
    return folder


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = JobStore(self.root)
        self.box = BoxStore(self.root)

    def test_list_shows_size_frames_and_saved_candidates(self):
        make_scan(self.store, "a")
        self.box.approve("a", 0, {"species": "하나몬"}, {"scan_id": "a", "individual": 0})
        (row,) = cleanup.scans(self.store, self.box.saved_counts())
        self.assertEqual((row["frames"], row["video"], row["individuals"], row["saved"], row["busy"], row["trimmed"]),
                         (6, True, 2, 1, False, False))
        self.assertGreater(row["bytes"], 5000 + 6000)

    def test_trim_keeps_results_detail_frames_and_evidence(self):
        folder = make_scan(self.store, "a")
        freed = cleanup.trim(self.store, "a")
        self.assertEqual(freed, 5000 + 4 * 1000)  # video and the four frames no card shows
        self.assertEqual(sorted(p.name for p in folder.glob("frame-*")), ["frame-002.jpg", "frame-004.jpg"])
        self.assertTrue((folder / "iv-01.jpg").is_file() and (folder / "analysis.json").is_file())
        self.assertFalse((folder / "source").exists())
        (row,) = cleanup.scans(self.store, {})
        self.assertTrue(row["trimmed"])
        self.assertEqual(cleanup.trim(self.store, "a"), 0)  # trimming twice frees nothing more

    def test_delete_removes_folder_and_row_but_not_the_box(self):
        make_scan(self.store, "a")
        entry = self.box.approve("a", 0, {"species": "하나몬"}, {"scan_id": "a", "individual": 0})
        self.assertGreater(cleanup.delete(self.store, "a"), 0)
        self.assertFalse((self.root / "a").exists())
        self.assertIsNone(self.store.get("a"))
        self.assertEqual(self.box.get(entry["id"])["species"], "하나몬")
        with self.assertRaises(cleanup.CleanupError):
            cleanup.delete(self.store, "a")

    def test_scans_in_use_are_left_alone(self):
        make_scan(self.store, "a")
        for status in ("running", "queued", "live"):
            self.store.set_analysis("a", status)
            for action in (cleanup.trim, cleanup.delete):
                with self.subTest(status=status, action=action.__name__), self.assertRaises(cleanup.CleanupError):
                    action(self.store, "a")
        self.assertTrue((self.root / "a" / "source").is_file())

    def test_reference_prune_keeps_current_and_the_previous_version(self):
        root = self.root / "reference"
        for version, fetched in (("aaa", "2026-11-01"), ("bbb", "2026-11-10"), ("ccc", "2026-11-05"), ("ddd", "2026-11-03")):
            (root / version).mkdir(parents=True)
            (root / version / "manifest.json").write_text(json.dumps({"fetched_at": fetched}), encoding="utf-8")
            (root / version / "reference.json").write_bytes(b"r" * 100)
        (root / "current").write_text("ccc", encoding="utf-8")  # rolled back to an older download
        versions = cleanup.reference_versions(self.root)
        self.assertEqual([(v["version"], v["keep"]) for v in versions],
                         [("ccc", True), ("bbb", True), ("ddd", False), ("aaa", False)])
        self.assertGreater(cleanup.prune_reference(self.root), 200)
        self.assertEqual(sorted(p.name for p in root.iterdir() if p.is_dir()), ["bbb", "ccc"])


class CleanupApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from boxscan.api import create_app
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.client = TestClient(create_app(self.root), base_url="http://localhost")
        self.addCleanup(self.client.close)
        self.store = JobStore(self.root)

    def test_list_trim_delete_and_prune(self):
        make_scan(self.store, "6f9c2b8e-0000-4000-8000-000000000001")
        scan_id = "6f9c2b8e-0000-4000-8000-000000000001"
        body = self.client.get("/api/scans").json()
        self.assertEqual([s["id"] for s in body["scans"]], [scan_id])
        self.assertEqual(body["reference"]["versions"], [])
        trimmed = self.client.post(f"/api/scans/{scan_id}/trim")
        self.assertEqual((trimmed.status_code, trimmed.json()["freed"]), (200, 9000))
        # The kept detail frame is still served for the candidate card.
        card = self.client.get(f"/api/scans/{scan_id}").json()["analysis"]["individuals"][0]
        self.assertEqual(self.client.get(card["detail_frame"]).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/scans/{scan_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/scans/{scan_id}").status_code, 409)
        self.assertEqual(self.client.get("/api/scans").json()["scans"], [])
        self.assertEqual(self.client.post("/api/reference/prune").json(), {"freed": 0})
        # Requests from another site are refused like other changes.
        self.assertEqual(self.client.post(f"/api/scans/{scan_id}/trim", headers={"Origin": "http://evil.test"}).status_code,
                         403)


if __name__ == "__main__":
    unittest.main()

import json
import sqlite3
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from boxscan import backup
from boxscan.api import create_app
from boxscan.box import BoxStore, Conflict
from boxscan.store import JobStore

SCAN = "33333333-3333-4333-8333-333333333333"
SOURCE = {"scan_id": SCAN, "individual": 0}


def fill(data_dir: Path, with_reference: bool = True) -> BoxStore:
    """A small synthetic data folder: two box entries, one scan with frames and a video."""
    box = BoxStore(data_dir)
    box.approve(SCAN, 0, {"species": "하나몬", "cp": 100}, SOURCE)
    box.approve(SCAN, 1, {"species": "두나몬", "cp": 200}, SOURCE)
    store = JobStore(data_dir)
    store.create(SCAN, "합성.mp4")
    folder = data_dir / SCAN
    folder.mkdir()
    (folder / "frame-001.jpg").write_bytes(b"\xff\xd8 frame")
    (folder / "analysis.json").write_text("{}", encoding="utf-8")
    (folder / "source").write_bytes(b"video bytes")
    (folder / "notes.txt").write_text("not part of a backup", encoding="utf-8")
    if with_reference:
        version = data_dir / "reference" / "abcdef012345"
        version.mkdir(parents=True)
        (version / "reference.json").write_text('{"version": "abcdef012345"}', encoding="utf-8")
        (version / "manifest.json").write_text("{}", encoding="utf-8")
        (data_dir / "reference" / "current").write_text("abcdef012345", encoding="utf-8")
    return box


class BackupTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.box = fill(self.data)

    def test_create_and_inspect_box_only(self):
        manifest = backup.create(self.data, app_version="9.9.9")
        path = Path(manifest["path"])
        self.assertTrue(backup.NAME.match(path.name))
        self.assertEqual(path.parent, (self.data / "backups").resolve())
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(sorted(archive.namelist()), [
                "box.sqlite3", "manifest.json",
                "reference/abcdef012345/manifest.json", "reference/abcdef012345/reference.json"])
        summary = backup.inspect(path)
        self.assertEqual(summary["box"], {"pokemon": 2, "removed": 0, "revisions": 2})
        self.assertEqual((summary["reference_version"], summary["with_scans"], summary["app_version"]),
                         ("abcdef012345", False, "9.9.9"))

    def test_scans_and_videos_are_optional(self):
        with zipfile.ZipFile(backup.create(self.data, with_scans=True)["path"]) as archive:
            names = set(archive.namelist())
        self.assertIn(f"scans/{SCAN}/frame-001.jpg", names)
        self.assertNotIn(f"scans/{SCAN}/source", names)
        self.assertNotIn(f"scans/{SCAN}/notes.txt", names)
        with zipfile.ZipFile(backup.create(self.data, with_scans=True, with_videos=True)["path"]) as archive:
            self.assertIn(f"scans/{SCAN}/source", archive.namelist())

    def test_inspect_rejects_tampered_or_foreign_files(self):
        good = Path(backup.create(self.data)["path"])
        cases = {}
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(tampered, "w") as dst:
            for name in src.namelist():
                data = src.read(name)
                dst.writestr(name, data + b"x" if name.startswith("reference/") else data)
        cases["손상"] = tampered
        escape = self.root / "escape.zip"
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(escape, "w") as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
            dst.writestr("../evil.txt", "x")
        cases["허용되지 않은"] = escape
        foreign = self.root / "foreign.zip"
        with zipfile.ZipFile(foreign, "w") as dst:
            dst.writestr("manifest.json", json.dumps({"format": "other", "format_version": 1, "files": {}}))
        cases["이 앱에서 만든"] = foreign
        not_zip = self.root / "plain.zip"
        not_zip.write_text("hello")
        cases["열 수 없습니다"] = not_zip
        for expected, path in cases.items():
            with self.subTest(case=expected), self.assertRaises(backup.BackupError) as raised:
                backup.inspect(path)
            self.assertIn(expected, str(raised.exception))

    def test_restore_saves_current_box_first_and_merges_scans(self):
        saved = Path(backup.create(self.data, with_scans=True, with_videos=True)["path"])
        self.box.approve(SCAN, 2, {"species": "세나몬", "cp": 300}, SOURCE)  # made after the backup
        # A second data folder that has never seen the scan or the reference data.
        other = self.root / "other"
        BoxStore(other).approve("x", 0, {"species": "다른몬"}, SOURCE)
        result = backup.restore(other, saved)
        self.assertEqual(result["scans_added"], 1)
        self.assertEqual(sorted(e["species"] for e in BoxStore(other).list()), ["두나몬", "하나몬"])
        self.assertEqual((other / SCAN / "source").read_bytes(), b"video bytes")
        self.assertFalse((other / "scans").exists())
        self.assertEqual(JobStore(other).get(SCAN)["filename"], "합성.mp4")
        self.assertEqual((other / "reference" / "current").read_text(), "abcdef012345")
        safety = Path(result["safety_backup"])
        self.assertTrue(safety.name.startswith("before-restore-"))
        self.assertEqual(backup.inspect(safety)["box"]["pokemon"], 1)  # the box before restoring
        # Restoring into the original folder replaces the box but keeps the existing scan as is.
        (self.data / SCAN / "analysis.json").write_text('{"newer": true}', encoding="utf-8")
        result = backup.restore(self.data, saved)
        self.assertEqual(result["scans_added"], 0)
        self.assertEqual(len(self.box.list()), 2)
        self.assertEqual((self.data / SCAN / "analysis.json").read_text(), '{"newer": true}')
        with sqlite3.connect(self.data / "scans.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scans").fetchone()[0], 1)


class EditTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.box = BoxStore(Path(temporary.name))
        self.entry = self.box.approve("scan", 0, {
            "species": "하나몬", "cp": 100, "max_hp": 50, "shiny": False,
            "moves": {"fast": {"name": "A"}, "charged": [{"name": "B"}, {"name": "C"}]},
            "max_moves": [{"name": "다이월", "level": 1, "locked": False}],
        }, SOURCE)

    def test_edit_replaces_fields_and_keeps_history(self):
        edited = self.box.edit(self.entry["id"], {
            "species": "하나몬", "cp": 120, "max_hp": None, "shiny": True,
            "moves": {"fast": {"name": "A"}, "charged": [{"name": "B"}]},
        }, expected_revision=1, note="스페셜어택 2는 잘못 읽음")
        self.assertEqual((edited["revision"], edited["cp"], edited["max_hp"], edited["shiny"]), (2, 120, None, True))
        self.assertEqual(edited["moves"]["charged"], [{"name": "B"}])  # a value can be removed
        self.assertEqual(edited["max_moves"][0]["name"], "다이월")  # fields outside the form stay
        (latest, _) = self.box.get(self.entry["id"])["revisions"]
        self.assertEqual(latest["source"], {"kind": "manual_edit", "note": "스페셜어택 2는 잘못 읽음"})
        self.assertEqual(latest["before"]["cp"], 100)
        with self.assertRaises(Conflict):
            self.box.edit(self.entry["id"], {"cp": 130}, expected_revision=1)


class BackupApiTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / "data"
        self.box = fill(self.data, with_reference=False)
        self.client = TestClient(create_app(self.data), base_url="http://localhost")
        self.addCleanup(self.client.close)

    def test_create_list_download_preview_and_restore(self):
        created = self.client.post("/api/backups", json={"with_scans": True}).json()
        self.assertEqual((created["box"]["pokemon"], created["with_scans"], created["with_videos"]), (2, True, False))
        listed = self.client.get("/api/backups").json()["backups"]
        self.assertEqual(listed[0]["name"], created["name"])
        download = self.client.get(f"/api/backups/{created['name']}")
        self.assertEqual(download.headers["content-type"], "application/zip")
        self.assertEqual(self.client.get("/api/backups/..%2Fbox.sqlite3").status_code, 404)

        self.box.approve(SCAN, 5, {"species": "세나몬"}, SOURCE)
        preview = self.client.post("/api/backups/preview", content=download.content,
                                   headers={"Content-Type": "application/octet-stream"}).json()
        self.assertEqual(preview["summary"]["box"]["pokemon"], 2)
        self.assertEqual(len(self.box.list()), 3)  # preview changes nothing
        restored = self.client.post("/api/backups/restore", json={"token": preview["token"]}).json()
        self.assertTrue(restored["safety_backup"].startswith("before-restore-"))
        self.assertEqual(len(self.box.list()), 2)
        self.assertEqual(self.client.post("/api/backups/restore", json={"token": preview["token"]}).status_code, 404)

        bad = self.client.post("/api/backups/preview", content=b"not a zip",
                               headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(list((self.data / "backups" / "uploads").iterdir()), [])
        cross = self.client.post("/api/backups/restore", json={"name": created["name"]},
                                 headers={"Origin": "http://evil.test"})
        self.assertEqual(cross.status_code, 403)

    def test_patch_edits_with_revision_and_marks_typed_cp(self):
        entry = self.box.list()[0]
        body = {"expected_revision": entry["revision"], "values": {"species": "하나몬", "cp": 150}}
        edited = self.client.patch(f"/api/pokemon/{entry['id']}", json=body)
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual((edited.json()["cp"], edited.json()["cp_source"]), (150, "manual"))
        self.assertEqual(self.client.patch(f"/api/pokemon/{entry['id']}", json=body).status_code, 409)
        missing = self.client.patch(f"/api/pokemon/{entry['id']}", json={"values": {"cp": 150}})
        self.assertEqual(missing.status_code, 422)
        same = self.client.patch(f"/api/pokemon/{entry['id']}", json={
            "expected_revision": 2, "values": {"species": "하나몬", "cp": 150, "shiny": True}}).json()
        self.assertEqual((same["cp_source"], same["shiny"]), ("manual", True))
        from openpyxl import load_workbook
        import io
        sheet = load_workbook(io.BytesIO(self.client.get("/api/exports/box.xlsx").content))["포켓몬 목록"]
        headers = [cell.value for cell in sheet[1]]
        sources = [row[headers.index("CP 출처")].value for row in sheet.iter_rows(min_row=2)]
        self.assertIn("직접 입력", sources)

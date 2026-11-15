"""Full backup and restore of the approved box (spec v1.2 section 12).

A backup is one zip file:
- box.sqlite3: a consistent snapshot of the box (entries, revisions, approvals)
- reference/<version>/reference.json, manifest.json: the reference data the box was built with
- optional scans.sqlite3 and scans/<id>/...: review frames, IV evidence and analysis results,
  and with videos also each scan's original upload
- manifest.json: format version, counts and the SHA-256 of every file

Restoring checks the zip first (format, hashes, allowed file names, database integrity), then
writes a safety backup of the current box, then replaces box.sqlite3. Scans in a backup are added
only where they are missing; existing scans are never overwritten. No absolute paths are stored.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .box import DATABASE, BoxStore
from .store import JobStore

FORMAT = "boxscan-backup"
FORMAT_VERSION = 1
FOLDER = "backups"
NAME = re.compile(r"^(?:boxscan-backup|before-restore)-\d{8}-\d{6}(?:-\d+)?\.zip$")
# Every member must match one of these, so a crafted zip cannot write outside the data folder.
MEMBERS = [
    re.compile(r"^manifest\.json$"),
    re.compile(r"^box\.sqlite3$"),
    re.compile(r"^scans\.sqlite3$"),
    re.compile(r"^reference/[0-9a-f]{12}/(?:reference|manifest)\.json$"),
    re.compile(r"^scans/[0-9a-f-]{36}/(?:frame-\d{3}\.jpg|iv-\d{2}\.jpg|analysis\.json|source)$"),
]
MAX_TOTAL_BYTES = 8 * 1024 ** 3


class BackupError(Exception):
    """The backup file cannot be used. The message is shown to the user."""


def folder(data_dir: Path) -> Path:
    path = data_dir.resolve() / FOLDER
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _unique(path: Path) -> Path:
    candidate, number = path, 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        number += 1
    return candidate


def _snapshot(source: Path, target: Path) -> None:
    """Copy a SQLite database consistently, even while the app has it open."""
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _box_counts(database: Path) -> dict:
    with closing(sqlite3.connect(database)) as db:
        return {
            "pokemon": db.execute("SELECT COUNT(*) FROM pokemon WHERE deleted_at IS NULL").fetchone()[0],
            "removed": db.execute("SELECT COUNT(*) FROM pokemon WHERE deleted_at IS NOT NULL").fetchone()[0],
            "revisions": db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
        }


def create(data_dir: Path, output: Path | None = None, with_scans: bool = False,
           with_videos: bool = False, app_version: str = "unknown", prefix: str = "boxscan-backup") -> dict:
    """Write a backup zip and return its manifest with "path" added."""
    data_dir = data_dir.resolve()
    BoxStore(data_dir)  # makes sure the database exists, even when the box is empty
    output = _unique(output or folder(data_dir) / f"{prefix}-{_stamp()}.zip")
    files: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(dir=folder(data_dir)) as temporary:
        work = Path(temporary)
        _snapshot(data_dir / DATABASE, work / "box.sqlite3")
        files["box.sqlite3"] = work / "box.sqlite3"
        reference_version = None
        pointer = data_dir / "reference" / "current"
        if pointer.is_file():
            reference_version = pointer.read_text(encoding="utf-8").strip()
            for name in ("reference.json", "manifest.json"):
                path = data_dir / "reference" / reference_version / name
                if path.is_file():
                    files[f"reference/{reference_version}/{name}"] = path
        scans = 0
        if with_scans and (data_dir / "scans.sqlite3").is_file():
            _snapshot(data_dir / "scans.sqlite3", work / "scans.sqlite3")
            files["scans.sqlite3"] = work / "scans.sqlite3"
            for scan in sorted(p for p in data_dir.iterdir() if p.is_dir() and re.fullmatch(r"[0-9a-f-]{36}", p.name)):
                scans += 1
                for path in sorted(scan.iterdir()):
                    if path.name == "source" and not with_videos:
                        continue
                    arcname = f"scans/{scan.name}/{path.name}"
                    if path.is_file() and any(rule.match(arcname) for rule in MEMBERS):
                        files[arcname] = path
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "app_version": app_version,
            "box": _box_counts(files["box.sqlite3"]),
            "reference_version": reference_version,
            "with_scans": with_scans,
            "with_videos": with_scans and with_videos,
            "scans": scans,
            "files": {name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
                      for name, path in files.items()},
        }
        partial = output.with_name(output.name + ".partial")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, path in files.items():
                # Videos and JPEGs are already compressed.
                kind = zipfile.ZIP_STORED if name.endswith(("source", ".jpg")) else zipfile.ZIP_DEFLATED
                archive.write(path, name, compress_type=kind)
        partial.replace(output)
    return {**manifest, "path": str(output), "bytes": output.stat().st_size}


def inspect(path: Path) -> dict:
    """Check a backup without changing anything and return a preview summary."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise BackupError("백업 파일(zip)을 열 수 없습니다.") from None
    with archive, tempfile.TemporaryDirectory() as temporary:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise BackupError("백업 안에 같은 이름의 파일이 여러 개 있습니다.")
        for name in names:
            if not any(rule.match(name) for rule in MEMBERS):
                raise BackupError(f"백업에 허용되지 않은 파일이 있습니다: {name}")
        if sum(info.file_size for info in archive.infolist()) > MAX_TOTAL_BYTES:
            raise BackupError("백업 파일이 너무 큽니다.")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, ValueError):
            raise BackupError("백업 정보(manifest.json)가 없거나 읽을 수 없습니다.") from None
        if manifest.get("format") != FORMAT or manifest.get("format_version") != FORMAT_VERSION:
            raise BackupError("이 앱에서 만든 백업이 아니거나 지원하지 않는 버전입니다.")
        listed = manifest.get("files") or {}
        if set(listed) != set(names) - {"manifest.json"} or "box.sqlite3" not in listed:
            raise BackupError("백업 정보와 실제 파일 목록이 다릅니다.")
        for name, expected in listed.items():
            digest = hashlib.sha256()
            with archive.open(name) as member:
                for chunk in iter(lambda: member.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected.get("sha256"):
                raise BackupError(f"백업 파일이 손상됐거나 바뀌었습니다: {name}")
        box = Path(temporary) / "box.sqlite3"
        box.write_bytes(archive.read("box.sqlite3"))
        try:
            with closing(sqlite3.connect(box)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("백업 안의 박스 데이터베이스가 손상됐습니다.")
            counts = _box_counts(box)
        except sqlite3.DatabaseError:
            raise BackupError("백업 안의 박스 데이터베이스를 읽을 수 없습니다.") from None
    return {
        "created_at": manifest.get("created_at"),
        "app_version": manifest.get("app_version"),
        "box": counts,
        "reference_version": manifest.get("reference_version"),
        "with_scans": bool(manifest.get("with_scans")),
        "with_videos": bool(manifest.get("with_videos")),
        "scans": manifest.get("scans", 0),
    }


def restore(data_dir: Path, path: Path, app_version: str = "unknown") -> dict:
    """Replace the box with the backup's box after checking it and saving the current box."""
    data_dir = data_dir.resolve()
    summary = inspect(path)
    safety = create(data_dir, app_version=app_version, prefix="before-restore")
    added_scans = 0
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(dir=folder(data_dir)) as temporary:
        work = Path(temporary)
        staged = work / DATABASE
        staged.write_bytes(archive.read("box.sqlite3"))
        os.replace(staged, data_dir / DATABASE)
        names = archive.namelist()
        reference_root = data_dir / "reference"
        for name in names:
            if name.startswith("reference/"):
                target = data_dir / name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        if summary["reference_version"] and not (reference_root / "current").is_file():
            (reference_root / "current").write_text(summary["reference_version"], encoding="utf-8")
        if "scans.sqlite3" in names:
            (work / "scans.sqlite3").write_bytes(archive.read("scans.sqlite3"))
            JobStore(data_dir)  # current schema in the live database
            with closing(sqlite3.connect(work / "scans.sqlite3")) as old:
                old.row_factory = sqlite3.Row
                rows = [dict(row) for row in old.execute("SELECT * FROM scans")]
            with closing(sqlite3.connect(data_dir / "scans.sqlite3")) as live, live:
                columns = {row[1] for row in live.execute("PRAGMA table_info(scans)")}
                for row in rows:
                    if (data_dir / row["id"]).exists():
                        continue  # an existing scan is never overwritten
                    keep = {k: v for k, v in row.items() if k in columns}
                    cursor = live.execute(
                        f"INSERT OR IGNORE INTO scans ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})",
                        list(keep.values()))
                    if cursor.rowcount:
                        added_scans += 1
                        for name in names:
                            if name.startswith(f"scans/{row['id']}/"):
                                # Scan folders live directly in the data folder: data/<scan id>/...
                                target = data_dir / name.removeprefix("scans/")
                                target.parent.mkdir(parents=True, exist_ok=True)
                                with archive.open(name) as member, target.open("wb") as out:
                                    shutil.copyfileobj(member, out)
    return {"restored": summary, "safety_backup": safety["path"], "scans_added": added_scans}


def listing(data_dir: Path) -> list[dict]:
    entries = []
    for path in sorted(folder(data_dir).glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        if NAME.match(path.name):
            entries.append({"name": path.name, "bytes": path.stat().st_size,
                            "kind": "restore_safety" if path.name.startswith("before-restore") else "backup"})
    return entries

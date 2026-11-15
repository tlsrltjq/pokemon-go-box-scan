"""Approved personal box with revision history (spec v1.2 sections 4.2, 6 and 10).

Only values the user approved are stored here. Each approval of a scan candidate is recorded
once, so a double click or retry cannot create a second entry. Updating an existing entry keeps
its UUID and management code, bumps the revision and stores the previous values. Fields that a
new observation did not read keep their previous values instead of being cleared.
"""
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATABASE = "box.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS pokemon (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,  -- never reused, so codes stay unique after deletion
    id TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS revisions (
    pokemon_id TEXT NOT NULL REFERENCES pokemon(id),
    revision INTEGER NOT NULL,
    change TEXT NOT NULL CHECK (change IN ('created', 'updated', 'deleted')),
    before TEXT,
    after TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (pokemon_id, revision)
);
CREATE TABLE IF NOT EXISTS approvals (
    scan_id TEXT NOT NULL,
    individual INTEGER NOT NULL,
    pokemon_id TEXT NOT NULL REFERENCES pokemon(id),
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scan_id, individual)
);
"""


class Conflict(Exception):
    """The entry changed or was removed since the client read it."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def merge(old: dict, new: dict) -> dict:
    """Overlay newly approved values; a missing reading never erases an existing value."""
    merged = dict(old)
    for key, value in new.items():
        if value is None or value == [] or value == {}:
            continue
        if key == "moves":
            moves = dict(old.get("moves") or {})
            if value.get("fast"):
                moves["fast"] = value["fast"]
            if value.get("charged"):
                moves["charged"] = value["charged"]
            merged["moves"] = moves
        else:
            merged[key] = value
    return merged


class BoxStore:
    def __init__(self, data_dir: Path):
        self.database = data_dir.resolve() / DATABASE
        self.database.parent.mkdir(parents=True, exist_ok=True)
        # CREATE TABLE IF NOT EXISTS is safe when the server and the worker start together.
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _write(self):
        with closing(sqlite3.connect(self.database, isolation_level=None, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    def _read(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        return closing(db)

    @staticmethod
    def _entry(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "code": f"PK{row['seq']:04d}",
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            **json.loads(row["data"]),
        }

    def approval(self, scan_id: str, individual: int) -> dict | None:
        with self._read() as db:
            row = db.execute(
                "SELECT a.revision AS approved_revision, p.* FROM approvals a "
                "JOIN pokemon p ON p.id = a.pokemon_id WHERE a.scan_id = ? AND a.individual = ?",
                (scan_id, individual),
            ).fetchone()
        if row is None:
            return None
        return {"pokemon_id": row["id"], "code": f"PK{row['seq']:04d}",
                "revision": row["approved_revision"], "deleted": row["deleted_at"] is not None}

    def approve(self, scan_id: str, individual: int, values: dict, source: dict,
                target: str | None = None, expected_revision: int | None = None) -> dict:
        """Create a new entry, or update `target` when given. Returns the stored entry.

        Repeating the same (scan_id, individual) returns the earlier result with duplicate=True.
        """
        stamp = now()
        with self._write() as db:
            done = db.execute(
                "SELECT pokemon_id FROM approvals WHERE scan_id = ? AND individual = ?",
                (scan_id, individual),
            ).fetchone()
            if done is not None:
                row = db.execute("SELECT * FROM pokemon WHERE id = ?", (done["pokemon_id"],)).fetchone()
                return {**self._entry(row), "duplicate": True}
            if target is None:
                pokemon_id, revision, before = str(uuid.uuid4()), 1, None
                after = merge({}, values)
                db.execute(
                    "INSERT INTO pokemon (id, revision, data, created_at, updated_at) VALUES (?, 1, ?, ?, ?)",
                    (pokemon_id, json.dumps(after, ensure_ascii=False), stamp, stamp),
                )
                change = "created"
            else:
                row = db.execute("SELECT * FROM pokemon WHERE id = ?", (target,)).fetchone()
                if row is None or row["deleted_at"] is not None:
                    raise Conflict("갱신할 개체가 박스에 없습니다.")
                if expected_revision is not None and row["revision"] != expected_revision:
                    raise Conflict("다른 곳에서 먼저 갱신된 개체입니다. 최신 값을 다시 확인하세요.")
                pokemon_id, revision = target, row["revision"] + 1
                before = json.loads(row["data"])
                after = merge(before, values)
                db.execute(
                    "UPDATE pokemon SET revision = ?, data = ?, updated_at = ? WHERE id = ?",
                    (revision, json.dumps(after, ensure_ascii=False), stamp, pokemon_id),
                )
                change = "updated"
            db.execute(
                "INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pokemon_id, revision, change,
                 json.dumps(before, ensure_ascii=False) if before is not None else None,
                 json.dumps(after, ensure_ascii=False), json.dumps(source, ensure_ascii=False), stamp),
            )
            db.execute("INSERT INTO approvals VALUES (?, ?, ?, ?, ?)",
                       (scan_id, individual, pokemon_id, revision, stamp))
            row = db.execute("SELECT * FROM pokemon WHERE id = ?", (pokemon_id,)).fetchone()
            return {**self._entry(row), "duplicate": False}

    def list(self, species: str | None = None) -> list[dict]:
        with self._read() as db:
            rows = db.execute("SELECT * FROM pokemon WHERE deleted_at IS NULL ORDER BY seq").fetchall()
        entries = [self._entry(row) for row in rows]
        if species:
            entries = [entry for entry in entries if entry.get("species") == species]
        return entries

    def get(self, pokemon_id: str) -> dict | None:
        with self._read() as db:
            row = db.execute("SELECT * FROM pokemon WHERE id = ?", (pokemon_id,)).fetchone()
            if row is None or row["deleted_at"] is not None:
                return None
            revisions = db.execute(
                "SELECT * FROM revisions WHERE pokemon_id = ? ORDER BY revision DESC", (pokemon_id,)
            ).fetchall()
        entry = self._entry(row)
        entry["revisions"] = [
            {"revision": r["revision"], "change": r["change"], "created_at": r["created_at"],
             "before": json.loads(r["before"]) if r["before"] else None,
             "after": json.loads(r["after"]) if r["after"] else None,
             "source": json.loads(r["source"]) if r["source"] else None}
            for r in revisions
        ]
        return entry

    def delete(self, pokemon_id: str, expected_revision: int) -> None:
        """Remove from the box but keep the row, history and code reservation."""
        stamp = now()
        with self._write() as db:
            row = db.execute("SELECT * FROM pokemon WHERE id = ?", (pokemon_id,)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise Conflict("이미 박스에 없는 개체입니다.")
            if row["revision"] != expected_revision:
                raise Conflict("다른 곳에서 먼저 갱신된 개체입니다. 최신 값을 다시 확인하세요.")
            revision = row["revision"] + 1
            db.execute("UPDATE pokemon SET revision = ?, deleted_at = ?, updated_at = ? WHERE id = ?",
                       (revision, stamp, stamp, pokemon_id))
            db.execute("INSERT INTO revisions VALUES (?, ?, 'deleted', ?, NULL, NULL, ?)",
                       (pokemon_id, revision, row["data"], stamp))

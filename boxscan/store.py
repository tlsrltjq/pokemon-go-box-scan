import sqlite3
from contextlib import closing
from pathlib import Path


class JobStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "scans.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS scans (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'ready', 'failed')
                    ),
                    error TEXT
                )
            """)

    def create(self, scan_id: str, filename: str) -> None:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "INSERT INTO scans (id, filename, status) VALUES (?, ?, 'queued')",
                (scan_id, filename),
            )

    def get(self, scan_id: str) -> dict | None:
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
            return dict(row) if row else None

    def claim(self) -> str | None:
        # The selection and the status change must happen in one write transaction.
        with closing(sqlite3.connect(self.database)) as db, db:
            row = db.execute("""
                UPDATE scans SET status = 'running'
                WHERE id = (
                    SELECT id FROM scans WHERE status = 'queued' ORDER BY rowid LIMIT 1
                )
                RETURNING id
            """).fetchone()
            return row[0] if row else None

    def finish(self, scan_id: str, error: str | None = None) -> None:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "UPDATE scans SET status = ?, error = ? WHERE id = ? AND status = 'running'",
                ("failed" if error else "ready", error, scan_id),
            )

    def frames(self, scan_id: str) -> list[Path]:
        return sorted((self.root / scan_id).glob("frame-*.jpg"))

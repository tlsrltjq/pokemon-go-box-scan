"""Approved personal box with revision history (spec v1.2 sections 4.2, 6 and 10).

Only values the user approved are stored here. Each approval of a scan candidate is recorded
once, so a double click or retry cannot create a second entry. Updating an existing entry keeps
its UUID and management code, bumps the revision and stores the previous values. Fields that a
new observation did not read keep their previous values instead of being cleared.

Management codes count up within each species and are shown with two digits (마기라스 01, 02, 망나뇽
01; 100 and up use three), so the nickname the user types stays short: 망나뇽01. A number is never given out twice in the same species:
removed entries keep theirs, and an entry whose species is corrected gets the next number of the
new species while the old number stays reserved.
"""
import json
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .forms import display_name

DATABASE = "box.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS pokemon (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,  -- insertion order
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
CREATE TABLE IF NOT EXISTS codes (
    species TEXT NOT NULL,  -- '' while the species is unknown
    number INTEGER NOT NULL,
    pokemon_id TEXT NOT NULL REFERENCES pokemon(id),
    current INTEGER NOT NULL,  -- 1 for the entry's code, 0 for a reserved earlier one
    PRIMARY KEY (species, number)
);
CREATE TABLE IF NOT EXISTS candies (
    family TEXT PRIMARY KEY,  -- the species the candy is named after: 애버라스 for 마기라스
    candy INTEGER,
    xl INTEGER,
    energy INTEGER,
    energy_kind TEXT,  -- 메가 or 원시
    updated_at TEXT NOT NULL,
    source TEXT
);
CREATE TABLE IF NOT EXISTS account (
    key TEXT PRIMARY KEY,  -- stardust
    value INTEGER,
    updated_at TEXT NOT NULL
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


# Every read joins the entry's current code.
SELECT = ("SELECT p.*, c.number AS code_number FROM pokemon p "
          "LEFT JOIN codes c ON c.pokemon_id = p.id AND c.current = 1")
# "01", "1", "001", and the earlier "PK1" / "PK0001" all name number 1.
CODE = re.compile(r"(?:PK)?\s?(\d{1,4})", re.IGNORECASE)


def code(number: int | None) -> str | None:
    return f"{number:02d}" if number else None


def code_number(value: str | None) -> int | None:
    match = CODE.fullmatch((value or "").strip())
    return int(match[1]) if match and int(match[1]) > 0 else None


# Fields the box page edits directly. They are replaced as a whole, so the user can clear a wrong
# value; other fields (mega record, max moves, approval history) stay as they are.
EDITABLE = ("species", "dex", "form", "form_name", "nickname", "mega_level", "mega_evolved", "mega_move_off", "shiny", "lucky", "background", "shadow", "max_state", "max_moves",
            "cp", "max_hp", "cp_source", "iv", "level", "moves", "fusion")


SENT_ONLY = ("max_moves", "mega_evolved", "mega_move_off")


class Conflict(Exception):
    """The entry changed or was removed since the client read it."""


class CodeTaken(Exception):
    """Another entry of the species holds the number; it can be swapped when the user agrees."""

    def __init__(self, holder: dict):
        super().__init__(f"{holder.get('species') or ''} {holder['code']}번은 다른 개체가 쓰고 있습니다.")
        self.holder = holder


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
        self._assign_missing()

    def _assign_missing(self) -> None:
        """Give codes to entries without one: a box from before per-species codes, or a restored backup."""
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            db.executescript(SCHEMA)
            missing = db.execute("SELECT 1 FROM pokemon p WHERE NOT EXISTS (SELECT 1 FROM codes c "
                                 "WHERE c.pokemon_id = p.id AND c.current = 1) LIMIT 1").fetchone()
        if missing:
            with self._write(assign=False) as db:
                rows = db.execute("SELECT id, data FROM pokemon p WHERE NOT EXISTS (SELECT 1 FROM codes c "
                                  "WHERE c.pokemon_id = p.id AND c.current = 1) ORDER BY seq").fetchall()
                for row in rows:
                    self._give_code(db, row["id"], json.loads(row["data"]).get("species"))

    @staticmethod
    def _give_code(db, pokemon_id: str, species: str | None) -> None:
        """Keep the current code if it is for this species, otherwise take the species' next number."""
        species = species or ""
        current = db.execute("SELECT species FROM codes WHERE pokemon_id = ? AND current = 1",
                             (pokemon_id,)).fetchone()
        if current is not None and current["species"] == species:
            return
        db.execute("UPDATE codes SET current = 0 WHERE pokemon_id = ?", (pokemon_id,))
        (last,) = db.execute("SELECT COALESCE(MAX(number), 0) FROM codes WHERE species = ?", (species,)).fetchone()
        db.execute("INSERT INTO codes VALUES (?, ?, ?, 1)", (species, last + 1, pokemon_id))

    @contextmanager
    def _write(self, assign: bool = True):
        if assign:  # the database file may have been replaced by a restore
            self._assign_missing()
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
        self._assign_missing()
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        return closing(db)

    @staticmethod
    def _entry(row: sqlite3.Row) -> dict:
        data = json.loads(row["data"])
        if data.get("dex") and data.get("form"):  # form labels added later apply to saved entries too
            data["form_name"] = display_name(data["dex"], data["form"]) or data.get("form_name")
        return {
            "id": row["id"],
            "code": code(row["code_number"]),
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            **data,
        }

    def record_resources(self, resources: dict, source: dict) -> None:
        """Keep the latest candy, XL, energy and stardust read on screen. A value that was not read
        keeps the saved one."""
        stamp = now()
        with self._write() as db:
            family = resources.get("family")
            if family:
                old = db.execute("SELECT * FROM candies WHERE family = ?", (family,)).fetchone()
                pick = lambda key: resources.get(key) if resources.get(key) is not None else (old[key] if old else None)
                db.execute("INSERT OR REPLACE INTO candies VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (family, pick("candy"), pick("xl"), pick("energy"), pick("energy_kind"), stamp,
                            json.dumps(source, ensure_ascii=False)))
            if resources.get("stardust") is not None:
                db.execute("INSERT OR REPLACE INTO account VALUES ('stardust', ?, ?)", (resources["stardust"], stamp))

    def set_candy(self, family: str, values: dict) -> dict:
        """Correct a family's candy by hand; every field is replaced (empty clears it)."""
        family = (family or "").strip()
        if not family or len(family) > 20:
            raise ValueError("사탕 이름을 확인하세요.")
        clean = {}
        for key in ("candy", "xl", "energy"):
            value = values.get(key)
            if value in (None, ""):
                clean[key] = None
            elif isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9_999_999:
                clean[key] = value
            elif isinstance(value, str) and value.strip().isdigit() and int(value) <= 9_999_999:
                clean[key] = int(value)
            else:
                raise ValueError("사탕 수는 0 이상의 숫자로 입력하세요.")
        kind = values.get("energy_kind")
        if kind not in (None, "", "메가", "원시"):
            raise ValueError("에너지 종류가 올바르지 않습니다.")
        with self._write() as db:
            db.execute("INSERT OR REPLACE INTO candies VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (family, clean["candy"], clean["xl"], clean["energy"], kind or None, now(),
                        json.dumps({"kind": "manual_edit"})))
        return self.candies()["candies"][family]

    def candies(self) -> dict:
        with self._read() as db:
            rows = db.execute("SELECT * FROM candies ORDER BY family").fetchall()
            dust = db.execute("SELECT * FROM account WHERE key = 'stardust'").fetchone()
        return {"candies": {row["family"]: {key: row[key] for key in ("candy", "xl", "energy", "energy_kind", "updated_at")}
                            for row in rows},
                "stardust": {"value": dust["value"], "updated_at": dust["updated_at"]} if dust else None}

    def saved_counts(self) -> dict[str, int]:
        """How many candidates of each scan were saved to the box (removed entries included)."""
        with self._read() as db:
            return dict(db.execute("SELECT scan_id, COUNT(*) FROM approvals GROUP BY scan_id").fetchall())

    def approval(self, scan_id: str, individual: int) -> dict | None:
        with self._read() as db:
            row = db.execute(
                f"SELECT a.revision AS approved_revision, r.change AS approved_change, r.source AS approved_source, "
                f"x.* FROM approvals a JOIN ({SELECT}) x ON x.id = a.pokemon_id "
                "LEFT JOIN revisions r ON r.pokemon_id = a.pokemon_id AND r.revision = a.revision "
                "WHERE a.scan_id = ? AND a.individual = ?",
                (scan_id, individual),
            ).fetchone()
        if row is None:
            return None
        source = json.loads(row["approved_source"]) if row["approved_source"] else {}
        return {"pokemon_id": row["id"], "code": code(row["code_number"]), "species": json.loads(row["data"]).get("species"),
                "revision": row["approved_revision"], "deleted": row["deleted_at"] is not None,
                "change": row["approved_change"], "auto": source.get("kind") == "auto_save",
                # Undo is offered while nothing changed the entry after this save.
                "undoable": row["deleted_at"] is None and row["revision"] == row["approved_revision"]}

    def undo_approval(self, scan_id: str, individual: int) -> dict:
        """Take back one save of a scan candidate while the entry is unchanged since: a new entry is
        removed (its number stays reserved), an update gets its earlier values back. The candidate
        can then be saved again. History keeps both steps."""
        stamp = now()
        with self._write() as db:
            approval = db.execute("SELECT * FROM approvals WHERE scan_id = ? AND individual = ?",
                                  (scan_id, individual)).fetchone()
            if approval is None:
                raise Conflict("저장 기록이 없는 후보입니다.")
            row = db.execute(f"{SELECT} WHERE p.id = ?", (approval["pokemon_id"],)).fetchone()
            if row is None or row["deleted_at"] is not None or row["revision"] != approval["revision"]:
                raise Conflict("저장한 뒤에 바뀌었거나 제거된 개체라 되돌리지 않습니다. 내 박스에서 고치세요.")
            saved = db.execute("SELECT * FROM revisions WHERE pokemon_id = ? AND revision = ?",
                               (row["id"], approval["revision"])).fetchone()
            revision = row["revision"] + 1
            source = json.dumps({"kind": "undo_approval", "scan_id": scan_id, "individual": individual},
                                ensure_ascii=False)
            if saved["change"] == "created":
                db.execute("UPDATE pokemon SET revision = ?, deleted_at = ?, updated_at = ? WHERE id = ?",
                           (revision, stamp, stamp, row["id"]))
                db.execute("INSERT INTO revisions VALUES (?, ?, 'deleted', ?, NULL, ?, ?)",
                           (row["id"], revision, row["data"], source, stamp))
                result = "removed"
            else:
                db.execute("UPDATE pokemon SET revision = ?, data = ?, updated_at = ? WHERE id = ?",
                           (revision, saved["before"], stamp, row["id"]))
                self._give_code(db, row["id"], json.loads(saved["before"]).get("species"))
                db.execute("INSERT INTO revisions VALUES (?, ?, 'updated', ?, ?, ?, ?)",
                           (row["id"], revision, row["data"], saved["before"], source, stamp))
                result = "restored"
            db.execute("DELETE FROM approvals WHERE scan_id = ? AND individual = ?", (scan_id, individual))
            return {"pokemon_id": row["id"], "result": result}

    def add(self, values: dict, note: str | None = None) -> dict:
        """A new entry typed in by hand (no scan). It gets the next number of its species."""
        stamp = now()
        with self._write() as db:
            pokemon_id = str(uuid.uuid4())
            after = merge({}, values)
            db.execute("INSERT INTO pokemon (id, revision, data, created_at, updated_at) VALUES (?, 1, ?, ?, ?)",
                       (pokemon_id, json.dumps(after, ensure_ascii=False), stamp, stamp))
            self._give_code(db, pokemon_id, after.get("species"))
            db.execute("INSERT INTO revisions VALUES (?, 1, 'created', NULL, ?, ?, ?)",
                       (pokemon_id, json.dumps(after, ensure_ascii=False),
                        json.dumps({"kind": "manual_add", "note": note}, ensure_ascii=False), stamp))
            return self._entry(db.execute(f"{SELECT} WHERE p.id = ?", (pokemon_id,)).fetchone())

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
                row = db.execute(f"{SELECT} WHERE p.id = ?", (done["pokemon_id"],)).fetchone()
                return {**self._entry(row), "duplicate": True}
            if target is None:
                pokemon_id, revision, before = str(uuid.uuid4()), 1, None
                after = merge({}, values)
                db.execute(
                    "INSERT INTO pokemon (id, revision, data, created_at, updated_at) VALUES (?, 1, ?, ?, ?)",
                    (pokemon_id, json.dumps(after, ensure_ascii=False), stamp, stamp),
                )
                self._give_code(db, pokemon_id, after.get("species"))
                change = "created"
            else:
                row = db.execute(f"{SELECT} WHERE p.id = ?", (target,)).fetchone()
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
                self._give_code(db, pokemon_id, after.get("species"))
                change = "updated"
            db.execute(
                "INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pokemon_id, revision, change,
                 json.dumps(before, ensure_ascii=False) if before is not None else None,
                 json.dumps(after, ensure_ascii=False), json.dumps(source, ensure_ascii=False), stamp),
            )
            db.execute("INSERT INTO approvals VALUES (?, ?, ?, ?, ?)",
                       (scan_id, individual, pokemon_id, revision, stamp))
            row = db.execute(f"{SELECT} WHERE p.id = ?", (pokemon_id,)).fetchone()
            return {**self._entry(row), "duplicate": False}

    def list(self, species: str | None = None) -> list[dict]:
        with self._read() as db:
            rows = db.execute(f"{SELECT} WHERE p.deleted_at IS NULL ORDER BY p.seq").fetchall()
        entries = [self._entry(row) for row in rows]
        if species:
            entries = [entry for entry in entries if entry.get("species") == species]
        return entries

    def by_code(self, species: str | None, value: str | None) -> dict | None:
        """The entry holding this code in this species, including removed ones (codes are never reused)."""
        number = code_number(value)
        if number is None or not species:
            return None
        with self._read() as db:
            row = db.execute(f"{SELECT} WHERE c.species = ? AND c.number = ?", (species, number)).fetchone()
        if row is None:
            return None
        return {**self._entry(row), "deleted": row["deleted_at"] is not None}

    def get(self, pokemon_id: str) -> dict | None:
        with self._read() as db:
            row = db.execute(f"{SELECT} WHERE p.id = ?", (pokemon_id,)).fetchone()
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

    def edit(self, pokemon_id: str, values: dict, expected_revision: int, note: str | None = None) -> dict:
        """Replace the editable fields after checking the revision; history keeps the old values."""
        stamp = now()
        with self._write() as db:
            row = db.execute(f"{SELECT} WHERE p.id = ?", (pokemon_id,)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise Conflict("박스에 없는 개체입니다.")
            if row["revision"] != expected_revision:
                raise Conflict("다른 곳에서 먼저 갱신된 개체입니다. 최신 값을 다시 확인하세요.")
            before = json.loads(row["data"])
            # 맥스기술 and the mega choices are replaced only when sent (the edit form sends them; other
            # callers keep them).
            after = {**before, **{key: values.get(key) for key in EDITABLE if key not in SENT_ONLY or key in values}}
            revision = row["revision"] + 1
            db.execute("UPDATE pokemon SET revision = ?, data = ?, updated_at = ? WHERE id = ?",
                       (revision, json.dumps(after, ensure_ascii=False), stamp, pokemon_id))
            self._give_code(db, pokemon_id, after.get("species"))
            db.execute(
                "INSERT INTO revisions VALUES (?, ?, 'updated', ?, ?, ?, ?)",
                (pokemon_id, revision, json.dumps(before, ensure_ascii=False),
                 json.dumps(after, ensure_ascii=False),
                 json.dumps({"kind": "manual_edit", "note": note}, ensure_ascii=False), stamp),
            )
            row = db.execute(f"{SELECT} WHERE p.id = ?", (pokemon_id,)).fetchone()
            return self._entry(row)

    def set_code(self, pokemon_id: str, number: int, expected_revision: int, swap: bool = False) -> dict:
        """Give the entry another number within its species, chosen by the user.

        A number reserved by a removed or re-classified entry may be chosen on purpose. A number another
        entry holds raises CodeTaken unless swap is True, in which case that entry takes the old number.
        The old number stays reserved, so it is not handed out automatically later. Both entries get a
        new revision that records the change.
        """
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 999:
            raise ValueError("관리 번호는 1~999 사이로 입력하세요.")
        stamp = now()
        with self._write() as db:
            row = db.execute(f"{SELECT} WHERE p.id = ?", (pokemon_id,)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise Conflict("박스에 없는 개체입니다.")
            if row["revision"] != expected_revision:
                raise Conflict("다른 곳에서 먼저 갱신된 개체입니다. 최신 값을 다시 확인하세요.")
            species = json.loads(row["data"]).get("species") or ""
            old = row["code_number"]
            if old == number:
                return self._entry(row)
            target = db.execute("SELECT * FROM codes WHERE species = ? AND number = ?", (species, number)).fetchone()
            holder = None
            if target is not None and target["current"] == 1:
                holder = db.execute(f"{SELECT} WHERE p.id = ?", (target["pokemon_id"],)).fetchone()
                if not swap:
                    raise CodeTaken({**self._entry(holder), "deleted": holder["deleted_at"] is not None})
                if old is None:
                    raise Conflict("이 개체에 번호가 없어 맞바꿀 수 없습니다.")
                # Swap the two holders of the rows; both rows stay current.
                db.execute("UPDATE codes SET pokemon_id = ? WHERE species = ? AND number = ?", (holder["id"], species, old))
                db.execute("UPDATE codes SET pokemon_id = ? WHERE species = ? AND number = ?", (pokemon_id, species, number))
            else:
                db.execute("UPDATE codes SET current = 0 WHERE pokemon_id = ? AND current = 1", (pokemon_id,))
                if target is not None:  # a reserved number, chosen on purpose
                    db.execute("UPDATE codes SET pokemon_id = ?, current = 1 WHERE species = ? AND number = ?",
                               (pokemon_id, species, number))
                else:
                    db.execute("INSERT INTO codes VALUES (?, ?, ?, 1)", (species, number, pokemon_id))
            changes = [(row, old, number)] + ([(holder, number, old)] if holder is not None else [])
            for changed, before, after in changes:
                revision = changed["revision"] + 1
                db.execute("UPDATE pokemon SET revision = ?, updated_at = ? WHERE id = ?", (revision, stamp, changed["id"]))
                db.execute("INSERT INTO revisions VALUES (?, ?, 'updated', ?, ?, ?, ?)",
                           (changed["id"], revision, changed["data"], changed["data"],
                            json.dumps({"kind": "code_change", "from": code(before), "to": code(after),
                                        "swapped": holder is not None}, ensure_ascii=False), stamp))
            return self._entry(db.execute(f"{SELECT} WHERE p.id = ?", (pokemon_id,)).fetchone())

    def delete(self, pokemon_id: str, expected_revision: int) -> None:
        """Remove from the box but keep the row, history and code reservation."""
        stamp = now()
        with self._write() as db:
            row = db.execute(f"{SELECT} WHERE p.id = ?", (pokemon_id,)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise Conflict("이미 박스에 없는 개체입니다.")
            if row["revision"] != expected_revision:
                raise Conflict("다른 곳에서 먼저 갱신된 개체입니다. 최신 값을 다시 확인하세요.")
            revision = row["revision"] + 1
            db.execute("UPDATE pokemon SET revision = ?, deleted_at = ?, updated_at = ? WHERE id = ?",
                       (revision, stamp, stamp, pokemon_id))
            db.execute("INSERT INTO revisions VALUES (?, ?, 'deleted', ?, NULL, NULL, ?)",
                       (pokemon_id, revision, row["data"], stamp))

"""Small SQLite ledger preserving the complete exported show document."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TypeAlias

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Document: TypeAlias = dict[str, Json]
STATUSES: Final = ("archived", "blocked", "done", "ready", "review", "running", "scheduled", "todo", "triage")


def default_path() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "autophagy/ledger.db"


def stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def board_key(project_id: Json) -> str:
    """Hermes represents the default board with a null project_id; keep that in JSON."""
    return "__default__" if project_id is None else str(project_id)


class LedgerError(RuntimeError):
    """A local ledger request could not be applied."""


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Readers never create a database; writers transact and roll back on failure."""
        if write:
            if any((parent / ".git").exists() for parent in self.path.absolute().parents):
                raise LedgerError("LEDGER-REFUSED: runtime state must be outside a checkout")
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            os.close(fd)
        db = sqlite3.connect(self.path if write else self.path.as_uri() + "?mode=ro",
                             uri=not write, timeout=120)
        try:
            if write:
                db.executescript(
                    "CREATE TABLE IF NOT EXISTS boards (id TEXT PRIMARY KEY, slug TEXT UNIQUE, document TEXT NOT NULL);"
                    "CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, board TEXT NOT NULL, status TEXT NOT NULL,"
                    " dedup TEXT, document TEXT NOT NULL, UNIQUE(board, dedup));"
                    "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                )
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def active(self) -> bool:
        if not self.path.exists():
            return False
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
            receipt = db.execute("SELECT value FROM metadata WHERE key='migration'").fetchone()
            if row is not None and row[0] not in ("on", "off"):
                raise LedgerError("LEDGER-MODE-INVALID")
            if row is not None and row[0] == "on" and receipt is None:
                raise LedgerError("LEDGER-MIGRATION-RECEIPT-MISSING")
        return row is not None and row[0] == "on"

    def boards(self) -> list[Document]:
        if not self.path.exists():
            return []
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT document FROM boards ORDER BY slug")]

    def current_board(self) -> str:
        return next((str(board["slug"]) for board in self.boards() if board.get("is_current")), "default")

    def cards(self, board: str | None = None) -> list[Document]:
        if not self.path.exists():
            return []
        with self.connect() as db:
            rows = db.execute("SELECT document FROM cards ORDER BY id") if board is None else db.execute(
                "SELECT document FROM cards WHERE board=? ORDER BY id", (self.board_id(db, board),))
            return [json.loads(r[0])["task"] for r in rows]

    @staticmethod
    def board_id(db: sqlite3.Connection, board: str) -> str:
        row = db.execute("SELECT id FROM boards WHERE slug=? OR id=?", (board, board)).fetchone()
        if row is None:
            raise LedgerError("LEDGER-BOARD-NOT-FOUND")
        return str(row[0])

    def create_board(self, slug: str, name: str) -> Document:
        with self.connect(write=True) as db:
            row = db.execute("SELECT document FROM boards WHERE slug=?", (slug,)).fetchone()
            if row:
                return json.loads(row[0])
            document = {"project_id": "p_" + uuid.uuid4().hex[:12], "slug": slug,
                        "name": name or slug, "created_at": stamp(), "archived": False}
            db.execute("INSERT INTO boards VALUES (?,?,?)",
                       (document["project_id"], slug, json.dumps(document)))
        return document

    def create(self, title: str, *, board: str = "default", body: str = "",
               dedup: str | None = None, status: str = "ready", created_by: str = "") -> Document:
        if status not in STATUSES:
            raise LedgerError("LEDGER-STATUS-INVALID")
        if board == "default" and not any(b["slug"] == "default" for b in self.boards()):
            self.create_board("default", "Personal")
        with self.connect(write=True) as db:
            board_id = self.board_id(db, board)
            if dedup is not None:
                row = db.execute("SELECT document FROM cards WHERE board=? AND dedup=?", (board_id, dedup)).fetchone()
                if row:
                    return json.loads(row[0])["task"]
            project_id = json.loads(db.execute("SELECT document FROM boards WHERE id=?", (board_id,)).fetchone()[0])["project_id"]
            task = {"id": "t_" + uuid.uuid4().hex[:8], "project_id": project_id, "title": title,
                    "body": body, "status": status, "created_by": created_by, "created_at": stamp(),
                    "started_at": None, "completed_at": None, "result": None}
            document = {"task": task, "events": [], "comments": []}
            db.execute("INSERT INTO cards VALUES (?,?,?,?,?)",
                       (task["id"], board_id, status, dedup, json.dumps(document)))
        return task

    def show(self, card_id: str) -> Document:
        with self.connect() as db:
            row = db.execute("SELECT document FROM cards WHERE id=?", (card_id,)).fetchone()
        if row is None:
            raise LedgerError("LEDGER-CARD-NOT-FOUND")
        return json.loads(row[0])

    def change(self, card_id: str, operation: str, *, text: str = "", kind: str = "needs_input",
               author: str = "") -> None:
        with self.connect(write=True) as db:
            row = db.execute("SELECT document FROM cards WHERE id=?", (card_id,)).fetchone()
            if row is None:
                raise LedgerError("LEDGER-CARD-NOT-FOUND")
            document = json.loads(row[0])
            document["events"] = document.get("events") or []
            document["comments"] = document.get("comments") or []
            task = document["task"]
            at = stamp()
            match operation:
                case "block":
                    task["status"] = "blocked"
                    document["events"].append({"kind": "blocked", "created_at": at,
                                               "payload": {"kind": kind, "reason": text}})
                case "unblock":
                    task["status"] = "ready"
                    document["events"].append({"kind": "unblocked", "created_at": at, "payload": {}})
                case "complete":
                    task.update(status="done", result=text, completed_at=at)
                    document["events"].append({"kind": "completed", "created_at": at, "payload": {"result": text}})
                case "comment":
                    document["comments"].append({"author": author, "body": text, "created_at": at})
                case _:
                    raise LedgerError("LEDGER-OPERATION-INVALID")
            db.execute("UPDATE cards SET status=?,document=? WHERE id=?",
                       (task["status"], json.dumps(document), card_id))

"""Work orders the owner writes on the dashboard for the Doris (owner 2026-10-21 "대시보드에서 도리들에게
업무 지시를 할수있는 기능").

An order is the owner's own words plus what he linked (a todo, a project). The dashboard only records it;
the receiving Dori (``orders.receiver`` in the board config) is told by a relay outside this package, hands it
on in the Dori room, and writes back who took it, the room post and how far it got with ``order-update``.
The node gate reads one order through ``/api/orders/<n>`` to accept it as the owner's command and records each
use, so a write can say which order it came from.

The orders live in their own SQLite file next to the board (directory 0700, file 0600): the approved board
schema and its release-only migration stay untouched.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from .models import ProjectBoardError

ORDER_STATUSES: Final = ("받음", "넘김", "처리 중", "끝")
OPEN_ORDER_STATUSES: Final = frozenset(ORDER_STATUSES[:-1])
MAX_BODY: Final = 1000
MAX_DORI: Final = 20
MAX_URL: Final = 300
MAX_NOTE: Final = 300
DEFAULT_RECEIVER: Final = "관리 도리"
ORDERS_FILE: Final = "orders.db"

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS orders (
  number        INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at    TEXT NOT NULL,
  body          TEXT NOT NULL,
  todo_id       TEXT NOT NULL DEFAULT '',
  project       TEXT NOT NULL DEFAULT '',
  receiver      TEXT NOT NULL,
  created_by    TEXT NOT NULL,
  via           TEXT NOT NULL,
  status        TEXT NOT NULL,
  dori          TEXT NOT NULL DEFAULT '',
  post_url      TEXT NOT NULL DEFAULT '',
  note          TEXT NOT NULL DEFAULT '',
  updated_at    TEXT NOT NULL,
  delivered_at  TEXT NOT NULL DEFAULT '',
  delivered_to  TEXT NOT NULL DEFAULT '',
  uses          INTEGER NOT NULL DEFAULT 0,
  first_used_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS order_events (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  number INTEGER NOT NULL,
  at     TEXT NOT NULL,
  kind   TEXT NOT NULL,
  detail TEXT NOT NULL,
  actor  TEXT NOT NULL
);
"""
_CONTROL: Final = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_URL: Final = re.compile(r"https://[^\s\"'<>`]+")


@dataclass(frozen=True, slots=True)
class OrderEvent:
    at: str
    kind: str
    detail: str
    actor: str


@dataclass(frozen=True, slots=True)
class Order:
    number: int
    created_at: str
    body: str
    todo_id: str
    project: str
    receiver: str
    created_by: str
    via: str
    status: str
    dori: str
    post_url: str
    note: str
    updated_at: str
    delivered_at: str
    delivered_to: str
    uses: int
    first_used_at: str
    events: tuple[OrderEvent, ...] = ()

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_ORDER_STATUSES

    def as_dict(self, *, url: str = "") -> dict[str, object]:
        return {
            "number": self.number, "created_at": self.created_at, "body": self.body,
            "body_sha256": self.body_sha256, "todo_id": self.todo_id, "project": self.project,
            "receiver": self.receiver, "created_by": self.created_by, "via": self.via, "status": self.status,
            "dori": self.dori, "post_url": self.post_url, "note": self.note, "updated_at": self.updated_at,
            "delivered_at": self.delivered_at, "delivered_to": self.delivered_to, "uses": self.uses,
            "first_used_at": self.first_used_at, "url": url,
        }


def order_href(number: int) -> str:
    return f"#order/{number}"


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _line(value: str, limit: int, what: str) -> str:
    text = " ".join(value.split())
    if len(text) > limit:
        raise ProjectBoardError(f"{what} must be at most {limit} characters")
    return text


def clean_body(raw: str) -> str:
    """The owner's words as written: line breaks kept, other control characters and edge blanks dropped."""
    text = _CONTROL.sub("", raw.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text:
        raise ProjectBoardError("an order needs some words")
    if len(text) > MAX_BODY:
        raise ProjectBoardError(f"an order must be at most {MAX_BODY} characters")
    return text


def clean_url(raw: str) -> str:
    """A room post link: one https address, nothing a page could run."""
    text = raw.strip()
    if not text:
        return ""
    if len(text) > MAX_URL or not _URL.fullmatch(text):
        raise ProjectBoardError(f"post link must be one https:// address of at most {MAX_URL} characters")
    return text


class OrderStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fresh = not self.path.exists()
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            if fresh:
                self.path.chmod(0o600)
            connection.row_factory = sqlite3.Row
            connection.executescript(_SCHEMA)
            yield connection
            connection.commit()
        finally:
            connection.close()

    def add(
        self, body: str, *, receiver: str, by: str, via: str, todo_id: str = "", project: str = "",
        now: datetime | None = None,
    ) -> Order:
        text = clean_body(body)
        stamp = _stamp(now)
        who = _line(by, 120, "actor") or "알 수 없음"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "INSERT INTO orders (created_at, body, todo_id, project, receiver, created_by, via, status, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (stamp, text, todo_id, project, _line(receiver, 40, "receiver") or DEFAULT_RECEIVER, who, via,
                 ORDER_STATUSES[0], stamp),
            )
            number = int(cursor.lastrowid or 0)
            db.execute(
                "INSERT INTO order_events (number, at, kind, detail, actor) VALUES (?, ?, ?, ?, ?)",
                (number, stamp, "created", ORDER_STATUSES[0], who),
            )
        order = self.get(number)
        assert order is not None
        return order

    def update(
        self, number: int, *, status: str | None = None, dori: str | None = None, post_url: str | None = None,
        note: str | None = None, by: str, now: datetime | None = None,
    ) -> Order:
        if status is not None and status not in ORDER_STATUSES:
            raise ProjectBoardError(f"status must be one of {', '.join(ORDER_STATUSES)}")
        if status is None and dori is None and post_url is None and note is None:
            raise ProjectBoardError("nothing to record: give a status, a dori, a post link or a note")
        values = {
            "status": status,
            "dori": None if dori is None else _line(dori, MAX_DORI, "dori"),
            "post_url": None if post_url is None else clean_url(post_url),
            "note": None if note is None else _line(note, MAX_NOTE, "note"),
        }
        stamp = _stamp(now)
        who = _line(by, 120, "actor") or "알 수 없음"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone()
            if row is None:
                raise ProjectBoardError(f"no dashboard order #{number}")
            changes = {k: v for k, v in values.items() if v is not None and v != str(row[k])}
            if changes:
                sets = ", ".join(f"{k} = ?" for k in changes)
                db.execute(f"UPDATE orders SET {sets}, updated_at = ? WHERE number = ?",  # noqa: S608 - fixed column names
                           (*changes.values(), stamp, number))
                detail = " · ".join(
                    f"{row['status']}→{v}" if k == "status" else f"{k}={v}" for k, v in changes.items()
                )
                db.execute(
                    "INSERT INTO order_events (number, at, kind, detail, actor) VALUES (?, ?, ?, ?, ?)",
                    (number, stamp, "update", detail, who),
                )
        order = self.get(number)
        assert order is not None
        return order

    def mark_delivered(self, number: int, to: str, *, now: datetime | None = None) -> bool:
        """The relay told ``to``; only the first delivery is recorded (a re-run is a no-op)."""
        stamp = _stamp(now)
        receiver = _line(to, 40, "receiver")
        if not receiver:
            raise ProjectBoardError("say who received the order")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT delivered_at FROM orders WHERE number = ?", (number,)).fetchone()
            if row is None:
                raise ProjectBoardError(f"no dashboard order #{number}")
            if row["delivered_at"]:
                return False
            db.execute("UPDATE orders SET delivered_at = ?, delivered_to = ? WHERE number = ?",
                       (stamp, receiver, number))
            db.execute(
                "INSERT INTO order_events (number, at, kind, detail, actor) VALUES (?, ?, ?, ?, ?)",
                (number, stamp, "delivered", receiver, "자동 전달"),
            )
        return True

    def record_use(self, number: int, *, by: str, now: datetime | None = None) -> Order:
        """The gate accepted this order as the owner's command for one write."""
        stamp = _stamp(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT first_used_at FROM orders WHERE number = ?", (number,)).fetchone()
            if row is None:
                raise ProjectBoardError(f"no dashboard order #{number}")
            db.execute(
                "UPDATE orders SET uses = uses + 1, first_used_at = ? WHERE number = ?",
                (row["first_used_at"] or stamp, number),
            )
            db.execute(
                "INSERT INTO order_events (number, at, kind, detail, actor) VALUES (?, ?, ?, ?, ?)",
                (number, stamp, "used", "", _line(by, 120, "actor") or "gate"),
            )
        order = self.get(number)
        assert order is not None
        return order

    def get(self, number: int) -> Order | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone()
            if row is None:
                return None
            events = db.execute(
                "SELECT at, kind, detail, actor FROM order_events WHERE number = ? ORDER BY id", (number,)
            ).fetchall()
        return _order(row, events)

    def all(self, *, limit: int = 200) -> tuple[Order, ...]:
        """Newest first, each with its history (read-only when the file does not exist yet)."""
        if not self.path.exists():
            return ()
        with self._connect() as db:
            rows = db.execute("SELECT * FROM orders ORDER BY number DESC LIMIT ?", (limit,)).fetchall()
            events = db.execute("SELECT number, at, kind, detail, actor FROM order_events ORDER BY id").fetchall()
        by_number: dict[int, list[sqlite3.Row]] = {}
        for event in events:
            by_number.setdefault(int(event["number"]), []).append(event)
        return tuple(_order(row, by_number.get(int(row["number"]), [])) for row in rows)


def _order(row: sqlite3.Row, events: list[sqlite3.Row]) -> Order:
    return Order(
        number=int(row["number"]), created_at=str(row["created_at"]), body=str(row["body"]),
        todo_id=str(row["todo_id"]), project=str(row["project"]), receiver=str(row["receiver"]),
        created_by=str(row["created_by"]), via=str(row["via"]), status=str(row["status"]), dori=str(row["dori"]),
        post_url=str(row["post_url"]), note=str(row["note"]), updated_at=str(row["updated_at"]),
        delivered_at=str(row["delivered_at"]), delivered_to=str(row["delivered_to"]), uses=int(row["uses"]),
        first_used_at=str(row["first_used_at"]),
        events=tuple(OrderEvent(str(e["at"]), str(e["kind"]), str(e["detail"]), str(e["actor"])) for e in events),
    )

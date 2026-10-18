"""One transaction imports every board/card, verifies identity and counts, then routes."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

from .store import Document, Store, board_key


class MigrationError(RuntimeError):
    """The export and destination do not prove a lossless migration."""


def summary(boards: list[Document], cards: list[Document]) -> dict:
    return {"boards": len(boards), "cards": len(cards),
            "statuses": dict(sorted(Counter(card["task"]["status"] for card in cards).items()))}


def counts(db: sqlite3.Connection) -> dict:
    return {"boards": db.execute("SELECT count(*) FROM boards").fetchone()[0],
            "cards": db.execute("SELECT count(*) FROM cards").fetchone()[0],
            "statuses": dict(db.execute("SELECT status,count(*) FROM cards GROUP BY status ORDER BY status"))}


def import_snapshot(ledger: Store, boards: list[Document], cards: list[Document],
                    dedup: dict[str, str] | None = None) -> dict:
    """Preserve opaque export fields, not a lossy reconstruction of known fields."""
    expected = summary(boards, cards)
    fingerprint = hashlib.sha256(json.dumps([boards, cards, dedup or {}], sort_keys=True).encode()).hexdigest()
    board_ids = {board["project_id"] for board in boards}
    ids = [card["task"]["id"] for card in cards]
    if (len(set(ids)) != len(ids) or len(board_ids) != len(boards)
            or any(card["task"]["project_id"] not in board_ids for card in cards)):
        raise MigrationError("LEDGER-MIGRATION-REFUSED: duplicate or unbound source identity")
    with ledger.connect(write=True) as db:
        previous = db.execute("SELECT value FROM metadata WHERE key='migration'").fetchone()
        if previous:
            original = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
            if original is None or original[0] != fingerprint:
                raise MigrationError("LEDGER-MIGRATION-REFUSED: legacy source changed since first migration")
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('mode','on')")
            return json.loads(previous[0])
        if db.execute("SELECT count(*) FROM cards").fetchone()[0]:
            raise MigrationError("LEDGER-MIGRATION-REFUSED: destination is not empty")
        for board in boards:
            db.execute("INSERT INTO boards VALUES (?,?,?)",
                       (board_key(board["project_id"]), board["slug"], json.dumps(board)))
        for card in cards:
            task = card["task"]
            db.execute("INSERT INTO cards VALUES (?,?,?,?,?)",
                       (task["id"], board_key(task["project_id"]), task["status"],
                        (dedup or {}).get(task["id"]), json.dumps(card)))
        actual = counts(db)
        board_readback = [json.loads(row[0]) for row in db.execute("SELECT document FROM boards ORDER BY id")]
        card_readback = [json.loads(row[0]) for row in db.execute("SELECT document FROM cards ORDER BY id")]
        if (actual != expected
                or sorted(board_readback, key=lambda b: board_key(b["project_id"])) != sorted(boards, key=lambda b: board_key(b["project_id"]))
                or sorted(card_readback, key=lambda c: c["task"]["id"]) != sorted(cards, key=lambda c: c["task"]["id"])):
            raise MigrationError("LEDGER-MIGRATION-REFUSED: counts/statuses or card readback mismatch; callers unchanged")
        db.execute("INSERT INTO metadata VALUES ('migration',?)", (json.dumps(actual),))
        db.execute("INSERT INTO metadata VALUES ('source',?)", (fingerprint,))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('mode','on')")
    return actual


def export_hermes() -> tuple[list[Document], list[Document]]:
    """CLI export explicitly includes archived boards and archived tasks."""
    executable = str(Path.home() / ".local/bin/hermes")
    def read(*arguments: str):
        completed = subprocess.run(
            [executable, "kanban", *arguments], check=True, capture_output=True, text=True, timeout=120,
        )
        return json.loads(completed.stdout)
    boards = read("boards", "list", "--all", "--json")
    if not isinstance(boards, list):
        raise MigrationError("LEDGER-MIGRATION-REFUSED: invalid boards export")
    cards = []
    for board in boards:
        listed = read("--board", board["slug"], "list", "--archived", "--json")
        if not isinstance(listed, list):
            raise MigrationError("LEDGER-MIGRATION-REFUSED: invalid card export")
        for task in listed:
            shown = read("--board", board["slug"], "show", task["id"], "--json")
            if not isinstance(shown, dict) or shown.get("task") != task:
                raise MigrationError("LEDGER-MIGRATION-REFUSED: source changed while exporting")
            cards.append(shown)
    return boards, cards


def source_keys(boards: list[Document]) -> dict[str, str]:
    """The CLI omits idempotency keys. Read those through SQLite read-only, never write kanban.db."""
    keys = {}
    for board in boards:
        path = Path(str(board["db_path"]))
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT id,idempotency_key FROM tasks WHERE project_id IS ? AND idempotency_key IS NOT NULL",
                              (board["project_id"],))
            keys.update(rows)
        finally:
            db.close()
    return keys


def verify_source_counts(boards: list[Document], cards: list[Document]) -> None:
    """An export that omits archived tasks cannot pass merely by importing its own rows."""
    statuses: Counter[str] = Counter()
    for board in boards:
        db = sqlite3.connect(Path(str(board["db_path"])).as_uri() + "?mode=ro", uri=True)
        try:
            statuses.update(dict(db.execute(
                "SELECT status,count(*) FROM tasks WHERE project_id IS ? GROUP BY status", (board["project_id"],),
            )))
        finally:
            db.close()
    if dict(sorted(statuses.items())) != summary(boards, cards)["statuses"]:
        raise MigrationError("LEDGER-MIGRATION-REFUSED: source/export status counts mismatch; callers unchanged")

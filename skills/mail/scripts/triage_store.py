"""SQLite idempotency store for the mail triage pipeline (W4-2).

Reuses the W3-2 claim-before-send shape: a mail uid may be drafted only by the
process that wins the INSERT for its claim inside a BEGIN IMMEDIATE
transaction, so concurrent/duplicate 10-minute ticks produce at most one draft
per mail. `processed` marks terminal outcomes; `counters` tracks the
consecutive approved-send failure count that drives the mail-mode NO-GO
downgrade (2 consecutive failures → W4-2-runtime re-verdict).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# Historical DB columns stay in place; new writes use zero, with no verdict.
SEND_FAILURE_COUNTER = "consecutive_send_failures"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_claims (
    uid TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed (
    uid TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    sensitive INTEGER NOT NULL,
    action TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL,
    item_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_items (
    run_id INTEGER NOT NULL,
    item_no INTEGER NOT NULL,
    uid TEXT NOT NULL,
    subject TEXT NOT NULL,
    sender_masked TEXT NOT NULL,
    sensitive INTEGER NOT NULL,
    category TEXT NOT NULL,
    flags TEXT NOT NULL,
    summary TEXT NOT NULL,
    note TEXT NOT NULL,
    recv_date TEXT NOT NULL,
    reply_key TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    channel_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, item_no)
);
CREATE INDEX IF NOT EXISTS idx_digest_items_uid ON digest_items (uid);
CREATE TABLE IF NOT EXISTS digest_actions (
    message_id TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (message_id, action)
);
CREATE TABLE IF NOT EXISTS digest_body_threads (
    message_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
# Columns added after the first release: (name, DDL) appended in place on old DBs.
_DIGEST_ITEM_ADDED_COLUMNS = (
    ("reply_key", "reply_key TEXT NOT NULL DEFAULT ''"),
    ("message_id", "message_id TEXT NOT NULL DEFAULT ''"),
    ("channel_id", "channel_id TEXT NOT NULL DEFAULT ''"),
)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    connection.executescript(_SCHEMA)
    present = {row[1] for row in connection.execute("PRAGMA table_info(digest_items)")}
    for name, ddl in _DIGEST_ITEM_ADDED_COLUMNS:
        if name not in present:
            try:
                connection.execute(f"ALTER TABLE digest_items ADD COLUMN {ddl}")
            except sqlite3.OperationalError as error:  # a concurrent connection added it first
                if "duplicate column" not in str(error):
                    raise
    return connection


def claim_mail(db_path: Path, uid: str, claimed_at: str) -> bool:
    """Atomically claim a mail uid; True means this caller must process it."""
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO mail_claims (uid, claimed_at) VALUES (?, ?)",
                (uid, claimed_at),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return cursor.rowcount == 1


def release_mail(db_path: Path, uid: str) -> None:
    """Drop a claim after a failed processing pass so a later tick retries."""
    with _connect(db_path) as connection:
        connection.execute("DELETE FROM mail_claims WHERE uid = ?", (uid,))


def record_processed(
    db_path: Path, uid: str, *, category: str, action: str, processed_at: str
) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO processed (uid, category, sensitive, action, processed_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (uid, category, 0, action, processed_at),
        )


def is_processed(db_path: Path, uid: str) -> bool:
    if not db_path.exists():
        return False
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM processed WHERE uid = ?", (uid,)
        ).fetchone()
    return row is not None


def processed_rows(db_path: Path) -> list[tuple[str, str, int, str, str]]:
    if not db_path.exists():
        return []
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT uid, category, sensitive, action, processed_at FROM processed ORDER BY uid"
        ).fetchall()
    return [(str(r[0]), str(r[1]), int(r[2]), str(r[3]), str(r[4])) for r in rows]


def record_digest_run(db_path: Path, sent_at: str, items: list[dict[str, int | str]]) -> int:
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT INTO digest_runs (sent_at, item_count) VALUES (?, ?)",
                (sent_at, len(items)),
            )
            if cursor.lastrowid is None:
                raise sqlite3.Error("digest run insert did not return a run id")
            run_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO digest_items "
                "(run_id, item_no, uid, subject, sender_masked, sensitive, category, "
                "flags, summary, note, recv_date, reply_key, message_id, channel_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        item["item_no"],
                        item["uid"],
                        item["subject"],
                        item["sender_masked"],
                        0,
                        item["category"],
                        item["flags"],
                        item["summary"],
                        item["note"],
                        item["recv_date"],
                        str(item.get("reply_key") or ""),
                        str(item.get("message_id") or ""),
                        str(item.get("channel_id") or ""),
                    )
                    for item in items
                ],
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return run_id


def digested_uids(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    with _connect(db_path) as connection:
        rows = connection.execute("SELECT DISTINCT uid FROM digest_items").fetchall()
    return {str(row[0]) for row in rows}


def latest_digest_items(
    db_path: Path, run_id: int | None = None
) -> list[dict[str, int | str]]:
    if not db_path.exists():
        return []
    with _connect(db_path) as connection:
        selected_run_id = run_id
        if selected_run_id is None:
            latest = connection.execute(
                "SELECT run_id FROM digest_runs ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                return []
            selected_run_id = int(latest[0])
        rows = connection.execute(
            "SELECT item_no, uid, subject, sender_masked, sensitive, category, flags, "
            "summary, note, recv_date FROM digest_items "
            "WHERE run_id = ? ORDER BY item_no",
            (selected_run_id,),
        ).fetchall()
    return [
        {
            "item_no": int(row[0]),
            "uid": str(row[1]),
            "subject": str(row[2]),
            "sender_masked": str(row[3]),
            "category": str(row[5]),
            "flags": str(row[6]),
            "summary": str(row[7]),
            "note": str(row[8]),
            "recv_date": str(row[9]),
        }
        for row in rows
    ]


def recent_digest_cards(db_path: Path, *, since: str) -> list[dict[str, int | str]]:
    """Digest mail cards posted with a known channel by runs sent at or after ``since``."""
    if not db_path.exists():
        return []
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT i.run_id, i.item_no, i.uid, i.subject, i.message_id, i.channel_id, r.sent_at "
            "FROM digest_items i JOIN digest_runs r ON r.run_id = i.run_id "
            "WHERE i.message_id != '' AND i.channel_id != '' ORDER BY i.run_id, i.item_no",
        ).fetchall()
    floor = _instant(since)
    return [
        {
            "run_id": int(row[0]), "item_no": int(row[1]), "uid": str(row[2]),
            "subject": str(row[3]), "message_id": str(row[4]), "channel_id": str(row[5]),
        }
        for row in rows
        if _instant(str(row[6])) >= floor
    ]


def _instant(stamp: str) -> datetime:
    """An ISO stamp as an aware UTC instant; unparseable stamps sort as the distant past."""
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def claim_digest_action(
    db_path: Path, message_id: str, action: str, now: str, *, max_attempts: int,
) -> int:
    """Claim one (card, action) run; the attempt number (1, 2, …) or 0 when refused.

    Non-zero only for a first run or a capped retry.

    A row in any state but ``retry`` (``running``, ``done``, ``partial``, ``gave-up``)
    refuses the claim, so a reaction seen on every tick acts exactly once.
    """
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT status, attempts FROM digest_actions WHERE message_id = ? AND action = ?",
                (message_id, action),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO digest_actions VALUES (?, ?, 'running', 1, ?)",
                    (message_id, action, now),
                )
                claimed = 1
            elif str(row[0]) == "retry" and int(row[1]) < max_attempts:
                connection.execute(
                    "UPDATE digest_actions SET status = 'running', attempts = attempts + 1, "
                    "updated_at = ? WHERE message_id = ? AND action = ?",
                    (now, message_id, action),
                )
                claimed = int(row[1]) + 1
            else:
                claimed = 0
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return claimed


def finish_digest_action(
    db_path: Path, message_id: str, action: str, status: str, now: str,
) -> None:
    """Record a claimed run's outcome (``done``, ``partial`` or ``retry``)."""
    with _connect(db_path) as connection:
        connection.execute(
            "UPDATE digest_actions SET status = ?, updated_at = ? "
            "WHERE message_id = ? AND action = ?",
            (status, now, message_id, action),
        )


def digest_action_status(db_path: Path, message_id: str, action: str) -> str:
    """The (card, action) run's status, or ``""`` when it never ran."""
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT status FROM digest_actions WHERE message_id = ? AND action = ?",
            (message_id, action),
        ).fetchone()
    return str(row[0]) if row else ""


def digest_body_thread(db_path: Path, message_id: str) -> dict[str, str] | None:
    """The card's 📄 full-text thread: id, guild and how far it got (opened/linked/posted)."""
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT thread_id, guild_id, stage FROM digest_body_threads WHERE message_id = ?",
            (message_id,),
        ).fetchone()
    if row is None:
        return None
    return {"thread_id": str(row[0]), "guild_id": str(row[1]), "stage": str(row[2])}


def save_digest_body_thread(
    db_path: Path, message_id: str, thread: dict[str, str], now: str,
) -> None:
    """Record the card's full-text thread right after each step, so a retry resumes it."""
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT INTO digest_body_threads VALUES (?, ?, ?, ?, ?) ON CONFLICT(message_id) "
            "DO UPDATE SET thread_id = excluded.thread_id, guild_id = excluded.guild_id, "
            "stage = excluded.stage, updated_at = excluded.updated_at",
            (message_id, thread["thread_id"], thread["guild_id"], thread["stage"], now),
        )


def digest_item_by_reply_key(db_path: Path, reply_key: str) -> dict[str, int | str] | None:
    """The newest digest item shown with this reply key, or None."""
    if not reply_key or not db_path.exists():
        return None
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT run_id, item_no, uid, message_id, channel_id FROM digest_items "
            "WHERE reply_key = ? ORDER BY run_id DESC LIMIT 1",
            (reply_key,),
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": int(row[0]), "item_no": int(row[1]),
        "uid": str(row[2]), "message_id": str(row[3]), "channel_id": str(row[4]),
    }


def consecutive_send_failures(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT value FROM counters WHERE name = ?", (SEND_FAILURE_COUNTER,)
        ).fetchone()
    return int(row[0]) if row else 0


def bump_send_failures(db_path: Path) -> int:
    """Increment the consecutive-failure counter and return the new value."""
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT INTO counters (name, value) VALUES (?, 1)"
                " ON CONFLICT(name) DO UPDATE SET value = value + 1",
                (SEND_FAILURE_COUNTER,),
            )
            row = connection.execute(
                "SELECT value FROM counters WHERE name = ?", (SEND_FAILURE_COUNTER,)
            ).fetchone()
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return int(row[0])


def reset_send_failures(db_path: Path) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO counters (name, value) VALUES (?, 0)",
            (SEND_FAILURE_COUNTER,),
        )

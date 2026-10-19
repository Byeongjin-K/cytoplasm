"""Plan and record MailOn attachment archives without Drive access.

Keeping discovery, stable names, and the archive.db schema together lets the Drive
CLI stay an effect-only boundary while preserving the existing on-disk contract.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path.home()
SOURCE_DB = HOME / ".hermes/mailon-runtime/state/data/state.db"
RUNTIME_ATTACHMENTS = HOME / ".hermes/mailon-runtime/state/data/attachments"
STATE_DIR = HOME / ".hermes/mail-attachment-drive"
STATE_DB = STATE_DIR / "archive.db"
ROOT_PARTS = ("autophagy", "메일 첨부파일")
BOGUS_NAMES = frozenset({"Save", "Download all"})
MAX_REMOTE_NAME_BYTES = 240


class SyncError(RuntimeError):
    """Carry a stable code so the cron wrapper need not expose local details."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Attachment:
    """Freeze discovery metadata so upload planning cannot drift from recording."""

    key: str
    uid: str
    filename: str
    local: Path
    size: int
    mtime_ns: int
    year: str
    month: str
    mailbox: str


def folder_cache() -> Path:
    """Keep DriveClient's existing folder-id cache alongside the archive state."""
    return STATE_DIR / "folders.json"


def _safe_mailbox(value: str) -> str:
    lowered = value.strip().lower()
    return lowered if lowered in {"inbox", "sent"} else "other"


def _date_parts(raw: str | None, first_seen: str | None) -> tuple[str, str]:
    candidate = raw or first_seen or ""
    if len(candidate) >= 7 and candidate[:4].isdigit() and candidate[5:7].isdigit():
        return candidate[:4], candidate[5:7]
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}", f"{now.month:02d}"


def attachment_key(uid: str, filename: str) -> str:
    payload = f"{uid}\0{unicodedata.normalize('NFC', filename)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def folder_parts(item: Attachment) -> tuple[str, ...]:
    return (*ROOT_PARTS, item.year, item.month, item.mailbox)


def resolve_local(uid: str, filename: str, local_path: str | None) -> Path | None:
    """Reject links because an archive must not follow a changed local target."""
    candidates: list[Path] = []
    if local_path:
        supplied = Path(local_path).expanduser()
        candidates.append(supplied if supplied.is_absolute() else HOME / supplied)
    candidates.append(RUNTIME_ATTACHMENTS / uid / filename)
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve()
    return None


def discover() -> tuple[list[Attachment], int, int]:
    """Read successful MailOn rows so failed scraper artifacts never reach Drive."""
    if not SOURCE_DB.is_file():
        raise SyncError("source_db_missing", f"MailOn state DB not found: {SOURCE_DB}")
    source = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    rows = source.execute(
        """
        SELECT a.uid, a.filename, a.local_path, a.first_seen,
               m.recv_date, COALESCE(m.folder, 'other') AS folder
        FROM attachments AS a LEFT JOIN messages AS m ON m.uid = a.uid
        WHERE a.status = 'ok' ORDER BY a.uid, a.filename
        """
    ).fetchall()
    source.close()
    found: list[Attachment] = []
    bogus = missing = 0
    for row in rows:
        uid = str(row["uid"] or "")
        filename = unicodedata.normalize("NFC", str(row["filename"] or ""))
        if not uid or not filename:
            missing += 1
        elif filename in BOGUS_NAMES:
            bogus += 1
        elif (local := resolve_local(uid, filename, row["local_path"])) is None:
            missing += 1
        else:
            stat = local.stat()
            year, month = _date_parts(row["recv_date"], row["first_seen"])
            found.append(Attachment(
                attachment_key(uid, filename), uid, filename, local, stat.st_size,
                stat.st_mtime_ns, year, month, _safe_mailbox(str(row["folder"] or "")),
            ))
    found.sort(key=lambda item: (item.size, item.year, item.month, item.uid, item.filename))
    return found, bogus, missing


def state_connection() -> sqlite3.Connection:
    """Create the historical schema exactly before an executor can record success."""
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)
    db = sqlite3.connect(STATE_DB)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS archived (
          attachment_key TEXT PRIMARY KEY, uid TEXT NOT NULL, size_bytes INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL, drive_file_id TEXT NOT NULL,
          parent_id TEXT NOT NULL, archived_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS download_retries (
          attachment_key TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0,
          notified INTEGER NOT NULL DEFAULT 0
        )"""
    )
    db.commit()
    STATE_DB.chmod(0o600)
    return db


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clip_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    clipped = encoded[:limit]
    while clipped:
        try:
            return clipped.decode("utf-8")
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return "file"


# MailOn rows recorded before its scraper learned to split sizes keep the link text's size tail
# ("bg&str.wrl 37.86 MB", "file.mp4 547980148"); a Drive name ending in it loses its file type.
_SIZE_TAIL = re.compile(r"(?:\s*\(?\s*\d+(?:\.\d+)?\s*[KMG]B\s*\)?|\s+\d{4,})\s*$", re.IGNORECASE)


def display_filename(filename: str) -> str:
    """The attachment's own file name without a trailing size the mail page printed beside it."""
    trimmed = _SIZE_TAIL.sub("", filename).strip()
    return trimmed if "." in trimmed else filename


def remote_name(item: Attachment) -> str:
    """Bound UTF-8 names to Drive's limit without losing collision identity."""
    filename = display_filename(item.filename)
    # The saved copy carries the same tail ("bg&str.wrl 37.86 MB" has suffix ".86 MB").
    prefix, suffix = f"{item.uid}__", Path(filename).suffix or item.local.suffix
    budget = MAX_REMOTE_NAME_BYTES - len(prefix.encode("utf-8"))
    stem = filename[:-len(suffix)] if suffix and filename.endswith(suffix) else filename
    clipped = _clip_utf8(stem, max(16, budget - len(suffix.encode("utf-8")) - 15))
    if clipped != stem:
        clipped += f"_{item.key[:12]}"
    return prefix + clipped + suffix


def failure_code(error: BaseException) -> str:
    code = error.code if isinstance(error, SyncError) else type(error).__name__
    return code.replace("\n", "_")[:64] or "unknown"


def plan(
    state: sqlite3.Connection, items: list[Attachment], limit: int | None
) -> tuple[list[Attachment], int, dict[str, str]]:
    """Reuse unchanged rows and prior file IDs so retries remain idempotent."""
    pending: list[Attachment] = []
    prior_ids: dict[str, str] = {}
    skipped = 0
    for item in items:
        previous = state.execute(
            "SELECT size_bytes, mtime_ns, drive_file_id FROM archived WHERE attachment_key = ?",
            (item.key,),
        ).fetchone()
        if previous and int(previous[0]) == item.size and int(previous[1]) == item.mtime_ns:
            skipped += 1
        elif limit is None or len(pending) < limit:
            if previous and previous[2]:
                prior_ids[item.key] = str(previous[2])
            pending.append(item)
    return pending, skipped, prior_ids


def record(
    state: sqlite3.Connection, item: Attachment, digest: str, parent_id: str, file_id: str
) -> None:
    """Commit only verified Drive IDs, retaining the existing archive.db row shape."""
    state.execute(
        """
        INSERT INTO archived (attachment_key, uid, size_bytes, mtime_ns, sha256,
          drive_file_id, parent_id, archived_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(attachment_key) DO UPDATE SET uid=excluded.uid,
          size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns, sha256=excluded.sha256,
          drive_file_id=excluded.drive_file_id, parent_id=excluded.parent_id,
          archived_at=excluded.archived_at
        """,
        (item.key, item.uid, item.size, item.mtime_ns, digest, file_id, parent_id,
         datetime.now(timezone.utc).isoformat()),
    )
    state.commit()


def report(state: sqlite3.Connection, **counts: int | str) -> dict[str, Any]:
    archived_total = int(state.execute("SELECT COUNT(*) FROM archived").fetchone()[0])
    return {"archived_total": archived_total, **counts}

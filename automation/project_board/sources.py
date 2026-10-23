"""Read-only access to what v1 already collected: institution mail and finished minutes.

Mail comes from the MailOn runtime the mail skill syncs (``state.db`` rows plus the stored
markdown and downloaded attachment files). Paths resolve exactly like the mail wrapper's
(``MAIL_WRAPPER_REPO`` / ``MAIL_WRAPPER_DB`` override, else the deploy-managed
``~/.hermes/mailon-runtime/current``) so both read the same release. Nothing here writes
to that runtime. Minutes are read through the meeting skill's own boundary,
``meeting_minutes.finalized_view`` — the appendix below it is transcript material, not
something a meeting decided, and that cut is not copied here.
"""

from __future__ import annotations

import importlib
import logging
import re
import os
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from .models import ProjectBoardError, SourceRef
from .mail_watermark import MailWatermark, received_time

MAX_TEXT: Final = 12_000
_REPLY_PREFIX: Final = re.compile(r"^\s*(?:\[\s*)?(?:re|fw|fwd|회신|답장|전달)\s*(?:\]\s*)?[:：]?\s*", re.IGNORECASE)
MEETING_DIR_ENV: Final = "PROJECT_BOARD_MEETING_DIR"
_REPO: Final = Path(__file__).resolve().parents[2]
logger = logging.getLogger("project_board.sources")


_MINUTES_PREFIX: Final = re.compile(r"^\s*회의록\s*[-—–_:·]?\s*")
_DATED: Final = re.compile(r"\d{4}-\d{2}-\d{2}(?:[_ ]\d{4})?")
_SESSION: Final = re.compile(
    r"(?:\s*[-_·]?\s*\(?\s*(?:제\s*)?\d+\s*(?:차|회)\s*\)?|\s*\(\d+\)|\s*#\d+|\s*-\s*\d+)\s*$"
)


def meeting_key(title: str) -> str:
    """The meeting series a minutes file belongs to: title without date, 「회의록」 and session number."""
    text = _DATED.sub(" ", _MINUTES_PREFIX.sub("", " ".join(title.split())))
    text = " ".join(text.split()).strip(" -_·")
    while True:
        stripped = _SESSION.sub("", text).strip(" -_·")
        if stripped == text or not stripped:
            return text.casefold()
        text = stripped


def thread_key(subject: str) -> str:
    """The conversation a mail belongs to: its subject without reply/forward prefixes, case-folded."""
    text = " ".join(subject.split())
    while True:
        stripped = _REPLY_PREFIX.sub("", text, count=1)
        if stripped == text:
            return text.casefold()
        text = stripped


@dataclass(frozen=True, slots=True)
class SourceDocument:
    ref: SourceRef
    text: str


@dataclass(frozen=True, slots=True)
class MailRuntime:
    repo: Path
    db: Path


def mail_runtime(env: Mapping[str, str] | None = None) -> MailRuntime:
    source = os.environ if env is None else env
    home = Path(source.get("HOME") or Path.home())
    repo = Path(source.get("MAIL_WRAPPER_REPO") or home / ".hermes" / "mailon-runtime" / "current")
    resolved = repo.resolve() if repo.is_symlink() else repo
    return MailRuntime(resolved, Path(source.get("MAIL_WRAPPER_DB") or resolved / "data" / "state.db"))


def _connect(runtime: MailRuntime) -> sqlite3.Connection:
    if not runtime.db.is_file():
        raise ProjectBoardError("institution mail state.db is missing (mail sync has not run)")
    connection = sqlite3.connect(f"file:{runtime.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def new_mail(runtime: MailRuntime, *, after: MailWatermark, limit: int) -> list[tuple[str, datetime]]:
    """Inbox mail after the received cursor, ordered in UTC with UID ties.

    Parse before ordering or limiting: ISO offsets and naive local MailOn dates cannot
    be ordered as raw text. Missing/invalid dates are never treated as newly received.
    """
    connection = _connect(runtime)
    try:
        rows = connection.execute(
            "SELECT uid, recv_date FROM messages WHERE folder = 'inbox'",
        ).fetchall()
    finally:
        connection.close()
    found: list[tuple[str, datetime]] = []
    invalid = 0
    for row in rows:
        try:
            stamp = received_time(row["recv_date"])
        except (ValueError, TypeError, OverflowError):
            invalid += 1
            continue
        uid = str(row["uid"])
        if stamp > after.received_at or (stamp == after.received_at and uid not in after.uids):
            found.append((uid, stamp))
    if invalid:
        logger.warning("mail analysis skipped invalid received dates: count=%d", invalid)
    return sorted(found, key=lambda item: (item[1], item[0]))[:limit]


def recent_mail(runtime: MailRuntime, *, since: str, limit: int) -> list[str]:
    """Inbox uids received on or after ``since`` (ISO date), newest first."""
    connection = _connect(runtime)
    try:
        rows = connection.execute(
            "SELECT uid FROM messages WHERE folder = 'inbox' AND recv_date >= ? ORDER BY recv_date DESC, uid LIMIT ?",
            (since, limit),
        ).fetchall()
    finally:
        connection.close()
    return [str(row["uid"]) for row in rows]


def _inside(root: Path, relative: str) -> Path | None:
    """``relative`` under the runtime, allowing its ``data/`` symlink to the shared state directory.

    A deployed MailOn release links ``data`` to ``mailon-runtime/state/data``, so every stored
    mail resolves outside the release directory itself; anything resolving elsewhere is refused.
    """
    candidate = (root / relative).resolve()
    allowed = (root.resolve(), (root / "data").resolve())
    return candidate if any(candidate.is_relative_to(base) for base in allowed) and candidate.is_file() else None


def mail_markdown(runtime: MailRuntime, uid: str) -> str | None:
    connection = _connect(runtime)
    try:
        row = connection.execute("SELECT markdown_path FROM messages WHERE uid = ?", (uid,)).fetchone()
    finally:
        connection.close()
    if row is None or not row["markdown_path"]:
        return None
    path = _inside(runtime.repo, str(row["markdown_path"]))
    return None if path is None else path.read_text(encoding="utf-8", errors="replace")


def front_matter(document: str) -> tuple[dict[str, str], list[str], str]:
    if not document.startswith("---\n"):
        return {}, [], document
    head, _, body = document[4:].partition("\n---\n")
    fields: dict[str, str] = {}
    attachments: list[str] = []
    current = ""
    for line in head.splitlines():
        if line.startswith("  - ") and current == "attachments":
            attachments.append(line[4:].strip().strip('"').strip("'"))
            continue
        key, sep, value = line.partition(":")
        if sep:
            current = key.strip()
            fields[current] = value.strip().strip('"').strip("'")
    return fields, attachments, body


def read_mail(runtime: MailRuntime, uid: str) -> SourceDocument | None:
    document = mail_markdown(runtime, uid)
    if document is None:
        return None
    fields, attachments, body = front_matter(document)
    ref = SourceRef(
        kind="mail", source_id=uid, subject=fields.get("subject", "")[:300],
        sender=fields.get("from", "")[:200], date=fields.get("date", "")[:40],
        attachments=tuple(attachments),
    )
    return SourceDocument(ref, body.strip()[:MAX_TEXT])


def mail_attachment(runtime: MailRuntime, uid: str, name: str) -> Path | None:
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        return None
    connection = _connect(runtime)
    try:
        row = connection.execute(
            "SELECT status, local_path FROM attachments WHERE uid = ? AND filename = ?", (uid, name)
        ).fetchone()
    finally:
        connection.close()
    if row is None or row["status"] != "ok":
        return None
    stored = str(row["local_path"] or "")
    if stored and not Path(stored).is_absolute():
        found = _inside(runtime.repo, stored)
        if found is not None:
            return found
    return _inside(runtime.repo, f"data/attachments/{uid}/{name}")


def meeting_dir(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    override = (source.get(MEETING_DIR_ENV) or "").strip()
    return Path(override).expanduser() if override else Path.home() / "notes" / "meetings"


def _finalized_view(text: str) -> str:
    scripts = _REPO / "skills" / "meeting" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        minutes = importlib.import_module("meeting_minutes")
    except ImportError as error:
        raise ProjectBoardError("meeting minutes boundary (finalized_view) is unavailable") from error
    return str(minutes.finalized_view(text))


def read_meeting(path: Path) -> SourceDocument:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ProjectBoardError(f"meeting minutes unreadable: {path.name}") from error
    view = _finalized_view(text)
    subject = next((line[2:].strip() for line in view.splitlines() if line.startswith("# ")), path.stem)
    ref = SourceRef(kind="meeting", source_id=path.name, subject=subject[:300], date=path.stem[:10])
    return SourceDocument(ref, view.strip()[:MAX_TEXT])


def meeting_text(directory: Path, name: str) -> str | None:
    if "/" in name or name.startswith("."):
        return None
    path = _inside(directory, name)
    return None if path is None else _finalized_view(path.read_text(encoding="utf-8", errors="replace"))

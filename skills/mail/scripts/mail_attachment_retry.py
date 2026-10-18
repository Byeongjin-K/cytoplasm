"""Bounded recovery of missing MailOn attachment bytes before Drive planning."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

import mail_attachment_archive as archive

MAX_ATTEMPTS: Final = 5
RECENT_DAYS: Final = 14


def fetch_attachment(uid: str, filename: str) -> None:
    """Use MailOn's existing downloader in its credential-aware runtime environment."""
    from mail_wrapper_read import _cfg, build_subprocess_env

    cfg = _cfg()
    result = subprocess.run(
        [str(cfg["python"]), str(Path(__file__).resolve()), uid, filename],
        cwd=cfg["repo"], env=build_subprocess_env(cfg),
        capture_output=True, timeout=cfg["timeout"], check=False,
    )
    if result.returncode:
        raise archive.SyncError("attachment_fetch_failed")


def notify_exhausted(text: str) -> bool:
    """Delegate delivery to the sole owner facade; failed imports remain retryable."""
    from mail_attachment_drive_sync import _runtime_root

    root = str(_runtime_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
        from automation.owner_notice import notify_owner
    except ImportError:
        return False
    message = OwnerMessage(
        subject_key="mail-attachment-archive-exhausted",
        subject="기관메일 첨부 보관 실패",
        fact=text,
        location=Ref(scope="resource", search=("Drive", "기관메일 첨부 보관 폴더")),
        owner=Action("none"),
        agent_next="유돌이 원인과 시도 내역을 확인하고, 해결하지 못하면 페어 채널에서 짱도리에게 요청합니다.",
        recovery="not_applicable",
        detail=Result("executed"),
    )
    return notify_owner(text, message=message, fault=True)


def _recent(received: str | None, first_seen: int | str | None, now: datetime) -> bool:
    """Unknown, future or old dates cannot opt historical attachments into recovery."""
    try:
        candidate = str(received or first_seen or "")
        stamp = (datetime.fromtimestamp(int(candidate), timezone.utc) if candidate.isdecimal()
                 else datetime.fromisoformat(candidate))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return now - timedelta(days=RECENT_DAYS) <= stamp <= now


def _available(uid: str, filename: str) -> bool:
    """Read back MailOn's result rather than trusting the fetch process exit code."""
    with sqlite3.connect(f"file:{archive.SOURCE_DB}?mode=ro", uri=True) as source:
        row = source.execute(
            "SELECT status, local_path FROM attachments WHERE uid=? AND filename=?",
            (uid, filename),
        ).fetchone()
    return bool(
        row and row[0] == "ok" and archive.resolve_local(uid, filename, row[1]) is not None
    )


def recover(state: sqlite3.Connection) -> int:
    """Retry recent, unspent MailOn items once per tick; pending is not job failure."""
    if not archive.SOURCE_DB.is_file():
        return 0  # discover() owns the existing source_db_missing error.
    with sqlite3.connect(f"file:{archive.SOURCE_DB}?mode=ro", uri=True) as source:
        rows = source.execute(
            """SELECT a.uid, a.filename, a.status, a.local_path,
                      m.recv_date, a.first_seen, a.attempts
               FROM attachments AS a LEFT JOIN messages AS m ON a.uid=m.uid
               ORDER BY a.uid, a.filename"""
        ).fetchall()
    now = datetime.now(timezone.utc)
    exhausted: list[tuple[str, str]] = []
    for uid, filename, status, local, received, first_seen, mailon_attempts in rows:
        if not uid or not filename or filename in archive.BOGUS_NAMES:
            continue
        if (mailon_attempts or 0) >= MAX_ATTEMPTS or not _recent(received, first_seen, now):
            continue
        key = archive.attachment_key(uid, filename)
        if state.execute(
            "SELECT 1 FROM archived WHERE attachment_key=?", (key,)
        ).fetchone():
            continue
        if status == "ok" and archive.resolve_local(uid, filename, local) is not None:
            continue
        state.execute(
            "INSERT OR IGNORE INTO download_retries (attachment_key) VALUES (?)", (key,)
        )
        attempts, notified = state.execute(
            "SELECT attempts, notified FROM download_retries WHERE attachment_key=?", (key,)
        ).fetchone()
        if attempts < MAX_ATTEMPTS:
            # Commit the attempt before fetching so a killed tick cannot retry forever.
            attempts += 1
            state.execute(
                "UPDATE download_retries SET attempts=? WHERE attachment_key=?", (attempts, key)
            )
            state.commit()
            try:
                fetch_attachment(uid, filename)
            except (OSError, subprocess.SubprocessError, archive.SyncError):
                pass  # The persisted budget and read-back below own recovery.
            if _available(uid, filename):
                continue
        # Exhausted rows have their dedicated notice; do not make the cron wrapper
        # deliver the generic failure marker again on every later tick.
        if attempts >= MAX_ATTEMPTS and not notified:
            raw_date = str(received or first_seen)
            if raw_date.isdecimal():
                raw_date = datetime.fromtimestamp(int(raw_date), timezone.utc).date().isoformat()
            date = " ".join(raw_date[:10].split())
            name = " ".join(Path(filename).name.split())[:80]
            exhausted.append((key, f"{date} · {name}"))
    if exhausted:
        preview = " / ".join(label for _, label in exhausted[:3])
        text = f"기관메일 첨부 보관 실패(5회): {len(exhausted)}건 · {preview}"
        if notify_exhausted(text):
            state.executemany(
                "UPDATE download_retries SET notified=1 WHERE attachment_key=?",
                ((key,) for key, _ in exhausted),
            )
            state.commit()
    return 0


def main() -> int:
    """Re-fetch one persisted URL with MailOn's downloader, bypassing its spent budget."""
    # This subprocess runs with MailOn's Python, cwd and credentials, not the main venv.
    sys.path.insert(0, str(Path.cwd()))
    from mailon.config import load_config
    from mailon.login import login
    from mailon.main import _make_browser
    from mailon.scraper import InboxScraper, MAILON_ORIGIN
    from mailon.state import StateDB
    from urllib.parse import urljoin

    uid, filename = sys.argv[1:]
    cfg = load_config()
    db = StateDB(cfg.state_db_path)
    with sqlite3.connect(cfg.state_db_path) as source:
        row = source.execute(
            "SELECT href, size_bytes FROM attachments WHERE uid=? AND filename=?", (uid, filename)
        ).fetchone()
    if not row or not row[0]:
        return 1
    browser = _make_browser(cfg)
    try:
        login(browser, cfg)
        scraper = InboxScraper(browser, cfg.attachments_dir)
        href = urljoin(MAILON_ORIGIN, row[0])
        saved = scraper._download_attachment(uid, filename, href, declared_size=row[1])
        db.record_attachment(
            uid, filename=filename, href=href, status="ok",
            size_bytes=saved.size, local_path=str(saved.local_path),
        )
    finally:
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

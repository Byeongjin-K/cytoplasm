"""Bounded recovery of missing MailOn attachment bytes before Drive planning."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import mail_attachment_archive as archive

MAX_ATTEMPTS: Final = 5


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
        from automation.owner_notice import notify_owner
    except ImportError:
        return False
    return notify_owner(text)


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
    """Try each unresolved attachment once per tick, including historical failures."""
    if not archive.SOURCE_DB.is_file():
        return 0  # discover() owns the existing source_db_missing error.
    with sqlite3.connect(f"file:{archive.SOURCE_DB}?mode=ro", uri=True) as source:
        rows = source.execute(
            """SELECT a.uid, a.filename, a.status, a.local_path,
                      m.recv_date, a.first_seen
               FROM attachments AS a LEFT JOIN messages AS m ON a.uid=m.uid
               ORDER BY a.uid, a.filename"""
        ).fetchall()
    failed = 0
    for uid, filename, status, local, received, first_seen in rows:
        if not uid or not filename or filename in archive.BOGUS_NAMES:
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
        failed += int(attempts < MAX_ATTEMPTS)
        if attempts >= MAX_ATTEMPTS and not notified:
            raw_date = str(received or "")
            if not raw_date:
                try:
                    raw_date = datetime.fromtimestamp(int(first_seen), timezone.utc).date().isoformat()
                except (TypeError, ValueError, OverflowError, OSError):
                    raw_date = "날짜 미상"
            date = " ".join(raw_date[:10].split())
            name = " ".join(Path(filename).name.split())
            if notify_exhausted(f"기관메일 첨부 보관 실패(5회): {date} · {name}"):
                state.execute(
                    "UPDATE download_retries SET notified=1 WHERE attachment_key=?", (key,)
                )
                state.commit()
    return failed


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

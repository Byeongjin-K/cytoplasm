"""Attachment recovery starts from the historical MailOn and archive DB schemas."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))

import mail_attachment_archive as archive  # noqa: E402
import mail_attachment_drive_sync as syncer  # noqa: E402


class FakeDrive:
    """Store uploaded bytes and expose the same verification seam as DriveClient."""

    def __init__(self) -> None:
        self.uploads: list[bytes] = []

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str:
        return "folder"

    def verify_owner_only(self, file_id: str) -> None:
        return None

    def upsert_file(self, local: Path, name: str, parent: str, prior: str | None) -> dict:
        self.uploads.append(local.read_bytes())
        return {"id": "file"}

    def file_checksum(self, file_id: str) -> tuple[str, int]:
        data = self.uploads[-1]
        return hashlib.sha256(data).hexdigest(), len(data)


@pytest.fixture
def old_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed a recent recoverable row and the pre-migration archive table."""
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(syncer.retry, "datetime", Clock)
    source = tmp_path / "state.db"
    with sqlite3.connect(source) as db:
        db.executescript("""
            CREATE TABLE messages (uid TEXT PRIMARY KEY, recv_date TEXT, folder TEXT);
            CREATE TABLE attachments (
              uid TEXT, filename TEXT, local_path TEXT, first_seen INTEGER,
              status TEXT, attempts INTEGER, href TEXT, size_bytes INTEGER);
            INSERT INTO messages VALUES ('u1', '2026-10-15T10:00:00', 'inbox');
            INSERT INTO attachments VALUES
              ('u1','report.pdf',NULL,1792000000,'fail',0,'/attachment/1',3);
        """)
    state_dir = tmp_path / "archive"
    state_dir.mkdir()
    state_db = state_dir / "archive.db"
    with sqlite3.connect(state_db) as db:
        db.execute("""
            CREATE TABLE archived (
              attachment_key TEXT PRIMARY KEY, uid TEXT NOT NULL, size_bytes INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL, drive_file_id TEXT NOT NULL,
              parent_id TEXT NOT NULL, archived_at TEXT NOT NULL)
        """)
        db.execute("INSERT INTO archived VALUES ('prior','old',3,1,'sha','id','parent','date')")
    monkeypatch.setattr(archive, "SOURCE_DB", source)
    monkeypatch.setattr(archive, "STATE_DIR", state_dir)
    monkeypatch.setattr(archive, "STATE_DB", state_db)
    monkeypatch.setattr(archive, "RUNTIME_ATTACHMENTS", tmp_path / "attachments")
    return source


def _recover(source: Path) -> None:
    local = source.parent / "report.pdf"
    local.write_bytes(b"pdf")
    with sqlite3.connect(source) as db:
        db.execute("UPDATE attachments SET status='ok', local_path=?", (str(local),))


def test_failed_then_successful_retry_uploads_once(old_state: Path, monkeypatch) -> None:
    # Given: the first re-fetch still fails, the second returns the bytes.
    calls: list[tuple[str, str]] = []

    def fetch(uid: str, filename: str) -> None:
        calls.append((uid, filename))
        if len(calls) == 2:
            _recover(old_state)

    monkeypatch.setattr(syncer.retry, "fetch_attachment", fetch)
    drive = FakeDrive()
    # When: consecutive archive ticks run.
    results = [syncer.sync(client=drive) for _ in range(3)]
    # Then: only the recovered bytes are uploaded, once.
    assert [r["uploaded"] for r in results] == [0, 1, 0]
    assert drive.uploads == [b"pdf"]
    assert calls == [("u1", "report.pdf")] * 2


def test_cap_sends_one_notice_and_stops_fetching(old_state: Path, monkeypatch) -> None:
    # Given: every fetch fails.
    calls: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(syncer.retry, "fetch_attachment", lambda uid, name: calls.append(uid))
    monkeypatch.setattr(syncer.retry, "notify_exhausted", lambda text: notices.append(text) or True)
    # When: more ticks than the budget run.
    results = [syncer.sync(client=FakeDrive()) for _ in range(8)]
    # Then: five fetches and one private, single-line notice.
    assert len(calls) == 5
    assert len(notices) == 1
    assert "2026-10-15" in notices[0] and "report.pdf" in notices[0]
    assert "\n" not in notices[0] and "/attachment/" not in notices[0]
    assert [result["failed"] for result in results] == [0] * 8


def test_failed_notice_is_retried_next_tick(old_state: Path, monkeypatch) -> None:
    # Given: the first exhausted notice cannot be delivered.
    notices: list[str] = []
    monkeypatch.setattr(syncer.retry, "fetch_attachment", lambda uid, name: None)

    def notify(text: str) -> bool:
        notices.append(text)
        return len(notices) > 1

    monkeypatch.setattr(syncer.retry, "notify_exhausted", notify)
    # When: exhaustion and subsequent ticks run.
    for _ in range(8):
        syncer.sync(client=FakeDrive())
    # Then: delivery retries once and stops after success.
    assert len(notices) == 2
    assert notices[0] == notices[1]


def test_old_failed_row_is_retried_without_losing_archived_rows(old_state: Path, monkeypatch) -> None:
    # Given: the old MailOn retry counter is already exhausted.
    with sqlite3.connect(old_state) as db:
        db.execute("UPDATE attachments SET attempts=5")
    monkeypatch.setattr(syncer.retry, "fetch_attachment", lambda uid, name: _recover(old_state))
    drive = FakeDrive()
    # When: the first upgraded tick runs.
    result = syncer.sync(client=drive)
    # Then: MailOn exhaustion is out of scope, preserving the historical archive row.
    assert result["uploaded"] == 0
    with sqlite3.connect(archive.STATE_DB) as db:
        assert db.execute("SELECT COUNT(*) FROM archived").fetchone()[0] == 1


def test_missing_ok_file_is_refetched(old_state: Path, monkeypatch) -> None:
    # Given: MailOn claims success but its local file is gone.
    with sqlite3.connect(old_state) as db:
        db.execute("UPDATE attachments SET status='ok', local_path='/missing/report.pdf'")
    monkeypatch.setattr(syncer.retry, "fetch_attachment", lambda uid, name: _recover(old_state))
    # When: the archive tick runs.
    result = syncer.sync(client=FakeDrive())
    # Then: the missing bytes are fetched before upload.
    assert result["uploaded"] == 1


def test_dry_run_does_not_fetch_or_notify(old_state: Path, monkeypatch) -> None:
    # Given: an unresolved attachment.
    monkeypatch.setattr(syncer.retry, "fetch_attachment", lambda *args: pytest.fail("fetch"))
    monkeypatch.setattr(syncer.retry, "notify_exhausted", lambda *args: pytest.fail("notice"))
    # When: planning only.
    result = syncer.sync(client=FakeDrive(), dry_run=True)
    # Then: no upload is attempted.
    assert result["uploaded"] == 0


def test_fetch_adapter_uses_mailon_downloader_and_closes_browser(old_state, monkeypatch) -> None:
    # Given: fake MailOn runtime boundaries, with the real exhausted source DB.
    import mail_attachment_retry as retry

    calls: list[tuple] = []
    cfg = SimpleNamespace(state_db_path=old_state, attachments_dir=old_state.parent)
    browser = SimpleNamespace(close=lambda: calls.append(("closed",)))

    class Scraper:
        def __init__(self, actual_browser, directory):
            assert actual_browser is browser
            assert directory == cfg.attachments_dir

        def _download_attachment(self, uid, name, href, *, declared_size):
            calls.append((uid, name, href, declared_size))
            local = old_state.parent / name
            local.write_bytes(b"pdf")
            return SimpleNamespace(size=3, local_path=local)

    class State:
        def __init__(self, path):
            assert path == old_state

        def record_attachment(self, uid, **fields):
            with sqlite3.connect(old_state) as db:
                db.execute(
                    "UPDATE attachments SET status=?, local_path=? WHERE uid=? AND filename=?",
                    (fields["status"], fields["local_path"], uid, fields["filename"]),
                )

    modules = {
        "mailon.config": {"load_config": lambda: cfg},
        "mailon.login": {"login": lambda actual, config: None},
        "mailon.main": {"_make_browser": lambda config: browser},
        "mailon.scraper": {"InboxScraper": Scraper, "MAILON_ORIGIN": "https://mail.example"},
        "mailon.state": {"StateDB": State},
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "argv", ["retry", "u1", "report.pdf"])
    # When: the isolated fetch entry point runs.
    assert retry.main() == 0
    # Then: it uses the stored URL even at five old attempts, and closes its own browser.
    assert calls == [("u1", "report.pdf", "https://mail.example/attachment/1", 3), ("closed",)]
    assert retry._available("u1", "report.pdf")


def test_fetch_subprocess_receives_wrapper_credentials(old_state, monkeypatch) -> None:
    # Given: wrapper-only credentials and a fake subprocess boundary.
    import mail_attachment_retry as retry
    import mail_wrapper_read

    cfg = {"python": "/runtime/python", "repo": old_state.parent, "timeout": 17}
    monkeypatch.setattr(mail_wrapper_read, "_cfg", lambda: cfg)
    monkeypatch.setattr(
        mail_wrapper_read, "build_subprocess_env", lambda config: {"HOME": "/private", "TEST": "yes"}
    )
    calls: list[tuple] = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(retry.subprocess, "run", run)
    # When: the archive asks for one attachment.
    retry.fetch_attachment("u1", "report.pdf")
    # Then: the existing runtime and wrapper environment are explicitly forwarded.
    argv, kwargs = calls[0]
    assert argv[0] == "/runtime/python" and argv[-2:] == ["u1", "report.pdf"]
    assert kwargs["env"] == {"HOME": "/private", "TEST": "yes"}
    assert kwargs["cwd"] == old_state.parent and kwargs["timeout"] == 17


def test_owner_facade_gets_the_single_notice_line(old_state, monkeypatch) -> None:
    # Given: the central notice facade, intercepted before any network access.
    import mail_attachment_retry as retry
    from automation import owner_notice

    from automation.interop.owner_message import render, Ref

    notices: list[str] = []

    def fake(text: str, *, message, fault) -> bool:
        assert fault
        notices.append(render(message, destination=Ref(scope="channel", space="guild", guild_id="1", channel_id="2")))
        return False
    monkeypatch.setattr(owner_notice, "notify_owner", fake)
    # When: the facade reports failed delivery.
    delivered = retry.notify_exhausted("기관메일 첨부 보관 실패: 2026-10-15 · report.pdf")
    # Then: that result remains false so the caller keeps its pending notice.
    assert delivered is False
    assert len(notices) == 1 and "2026-10-15 · report.pdf" in notices[0]

"""Mailplug "대용량 첨부" links: fetched directly without webmail cookies, and retried past the old cap."""
from __future__ import annotations

import http.server
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name in ("pyotp", "dotenv", "bs4"):
    if name not in sys.modules:
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.TOTP = stub.load_dotenv = stub.BeautifulSoup = object  # type: ignore[attr-defined]
            sys.modules[name] = stub
sys.path.insert(0, str(ROOT / "skills/mail/vendor"))

from mailon import scraper as scraper_mod  # noqa: E402

BODY = b"pptx-bytes" * 1000


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_GET(self):  # noqa: N802
        _Handler.seen.append(dict(self.headers))
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


class _NoBrowser:
    def __getattr__(self, name):
        raise AssertionError(f"browser must not be used for a large-file link: {name}")


@pytest.fixture
def server(monkeypatch):
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(scraper_mod, "LARGE_FILE_HOSTS", frozenset({"127.0.0.1"}))
    _Handler.seen = []
    yield f"http://127.0.0.1:{httpd.server_address[1]}/download/x/fname/a.pptx"
    httpd.shutdown()


def _scraper(tmp_path):
    s = object.__new__(scraper_mod.InboxScraper)
    s.attachments_dir = tmp_path / "att"
    s.browser = _NoBrowser()
    return s


def test_a_large_file_link_downloads_directly_without_session_cookies(tmp_path, server):
    # Given / When: an attachment whose link is on the large-file host (size unknown, as in the real mail).
    saved = _scraper(tmp_path)._download_attachment("9785553", "a.pptx 254.34 KB", server)
    # Then: the bytes land on disk, the browser is never touched, and no Cookie leaves the webmail.
    assert saved.local_path.read_bytes() == BODY
    assert _Handler.seen and not any("Cookie" in h for h in _Handler.seen)


def test_an_exhausted_large_file_link_gets_retried_and_a_webmail_one_does_not(tmp_path, server):
    # Given: both rows already failed 5 times (the old in-page fetch failure).
    class _Db:
        recorded: list[tuple] = []

        def all_failed_attachments(self, max_attempts=5):
            rows = [{"uid": "1", "filename": "big.wrl", "href": server, "size_bytes": None, "attempts": 5},
                    {"uid": "2", "filename": "x.pdf", "href": "https://mailon.kr/a", "size_bytes": None, "attempts": 5}]
            return [r for r in rows if r["attempts"] < max_attempts]

        def record_attachment(self, uid, **fields):
            self.recorded.append((uid, fields["status"]))

    db = _Db()
    # When
    ok, failing = _scraper(tmp_path).retry_failed_attachments(db)
    # Then: only the large-file link is retried, and it succeeds.
    assert (ok, failing) == (1, 0)
    assert db.recorded == [("1", "ok")]


def test_the_real_mailplug_host_is_a_large_file_link():
    assert scraper_mod.is_large_file_link("https://filefree.mailplug.com/download/d9/HTML5/ukey/1/fname/a")
    assert not scraper_mod.is_large_file_link("https://mailon.kr/mail/download?x=1")
    assert not scraper_mod.is_large_file_link("https://evil.example/filefree.mailplug.com")


# --- 2026-10-21: one sync tick must not wait on a slow large-file server ---------------------

class _StallHandler(http.server.BaseHTTPRequestHandler):
    """Sends 1 KiB of a much longer file, then stops sending (an external host stuck at 8 MiB)."""
    release = threading.Event()

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(10 * 1024 * 1024))
        self.end_headers()
        self.wfile.write(b"x" * 1024)
        self.wfile.flush()
        _StallHandler.release.wait(30)

    def log_message(self, *args):
        pass


@pytest.fixture
def stalled(monkeypatch):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StallHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(scraper_mod, "LARGE_FILE_HOSTS", frozenset({"127.0.0.1"}))
    _StallHandler.release.clear()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/download/x/fname/b.pptx"
    _StallHandler.release.set()
    httpd.shutdown()


def test_a_stalled_large_download_ends_at_the_sync_budget_and_leaves_no_part_file(
        tmp_path, stalled, monkeypatch):
    # Given: a sync scraper with a budget, and a server that stops sending after 1 KiB.
    monkeypatch.setattr(scraper_mod, "BUDGETED_READ_TIMEOUT_S", 0.5)
    s = _scraper(tmp_path)
    s.large_download_budget_s = 2.0
    started = __import__("time").monotonic()
    # When / Then: the download gives up instead of waiting for the 300 s read timeout.
    with pytest.raises(RuntimeError):
        s._download_attachment("9831024", "b.pptx", stalled)
    assert __import__("time").monotonic() - started < 10
    assert not list((tmp_path / "att" / "9831024").glob("*"))


def _slow_clock(monkeypatch):
    """Only the scraper's clock moves 40 s per call; the real time module is untouched."""
    import time as real_time
    clock = iter(range(0, 1_000_000, 40))
    monkeypatch.setattr(scraper_mod, "time", types.SimpleNamespace(
        monotonic=lambda: next(clock), time=real_time.time, sleep=real_time.sleep))


def test_a_trickling_large_download_stops_once_its_total_budget_is_spent(
        tmp_path, server, monkeypatch):
    # Given: every read looks 40 s later than the last (a file arriving at ~32 KB/s).
    _slow_clock(monkeypatch)
    monkeypatch.setattr(scraper_mod, "URLLIB_CHUNK_SIZE", 1000)
    s = _scraper(tmp_path)
    s.large_download_budget_s = 90.0
    # When / Then: the total budget, not the per-read timeout, ends it.
    with pytest.raises(RuntimeError, match="unfinished after 90s"):
        s._download_attachment("9831024", "a.pptx", server)
    assert not list((tmp_path / "att" / "9831024").glob("*"))


def test_without_a_budget_the_archive_recovery_still_downloads_the_whole_file(
        tmp_path, server, monkeypatch):
    # Given: the same slow clock, but no budget (mail_attachment_retry.py's scraper).
    _slow_clock(monkeypatch)
    monkeypatch.setattr(scraper_mod, "URLLIB_CHUNK_SIZE", 1000)
    saved = _scraper(tmp_path)._download_attachment("9831024", "a.pptx", server)
    assert saved.local_path.read_bytes() == BODY


def _db_with_failure(tmp_path, href, *, seconds_ago):
    from mailon.state import StateDB
    db = StateDB(tmp_path / "state.db")
    db.record_attachment("9830273", filename="c.hwpx", href=href, status="fail",
                         error_msg="The read operation timed out")
    with db._conn() as c:
        c.execute("UPDATE attachments SET last_attempt = last_attempt - ?", (seconds_ago,))
        c.commit()
    return db


def test_a_large_file_that_just_failed_is_not_retried_on_the_next_tick(tmp_path, server):
    # Given: the file failed 10 minutes ago (one tick).
    db = _db_with_failure(tmp_path, server, seconds_ago=600)
    # When
    result = _scraper(tmp_path).retry_failed_attachments(db)
    # Then: the tick does not touch it, and its attempt count stays.
    assert result == (0, 0)
    assert db.attachment_record("9830273", "c.hwpx")["attempts"] == 1


def test_a_large_file_whose_wait_is_over_is_retried(tmp_path, server):
    db = _db_with_failure(tmp_path, server, seconds_ago=31 * 60)
    assert _scraper(tmp_path).retry_failed_attachments(db) == (1, 0)
    assert db.attachment_record("9830273", "c.hwpx")["status"] == "ok"


def test_retry_wait_doubles_per_attempt_up_to_six_hours():
    now = 1_000_000
    due = scraper_mod.retry_due
    assert due(None, now)
    assert not due({"status": "fail", "attempts": 2, "last_attempt": now - 59 * 60}, now)
    assert due({"status": "fail", "attempts": 2, "last_attempt": now - 60 * 60}, now)
    assert not due({"status": "fail", "attempts": 139, "last_attempt": now - 5 * 3600}, now)
    assert due({"status": "fail", "attempts": 139, "last_attempt": now - 6 * 3600}, now)

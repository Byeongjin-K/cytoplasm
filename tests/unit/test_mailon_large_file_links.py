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

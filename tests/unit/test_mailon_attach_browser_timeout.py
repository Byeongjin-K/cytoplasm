"""A webmail attachment the in-page fetch cannot finish in agent-browser's fixed 30 s CDP limit.

2026-11-16: one xlsx of a KIMM mail failed three syncs and five archive retries with
``CDP command timed out: Runtime.evaluate`` while the other attachment of the same mail saved.
agent-browser stops any ``eval`` after 30 s whatever AGENT_BROWSER_DEFAULT_TIMEOUT says (node
check: a 35 s promise ends at 30.6 s with the default 25 s and with 120 s), so the in-page
fetch + base64 path can never deliver a file the webmail serves slower than that. Separate file
because test_mailon_large_file_links.py covers the cookie-less large-file links.
"""
from __future__ import annotations

import http.server
import json
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
from mailon.browser import BrowserError  # noqa: E402

BODY = b"xlsx-bytes" * 5000


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_GET(self):  # noqa: N802
        _Handler.seen.append(dict(self.headers))
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _Handler.seen = []
    yield f"http://127.0.0.1:{httpd.server_address[1]}/mail/manage/attach/download?mailUid=1&position=7"
    httpd.shutdown()


class _SlowEvalBrowser:
    """agent-browser as measured on the node: the eval dies at the CDP limit, the session lives on."""

    def __init__(self, error: str) -> None:
        self.error = error

    def eval_js(self, js: str) -> str:
        raise BrowserError(f"agent-browser failed (1): cmd=eval --stdin stderr={self.error}",
                           command="eval")

    def save_state(self, path) -> None:
        Path(path).write_text(json.dumps({"cookies": [
            {"name": "JSESSIONID", "value": "s1", "domain": "mailon.kr"},
            {"name": "other", "value": "x", "domain": "example.org"},
        ]}), encoding="utf-8")


def _scraper(tmp_path, browser):
    s = object.__new__(scraper_mod.InboxScraper)
    s.attachments_dir = tmp_path / "att"
    s.browser = browser
    s.large_download_budget_s = 90.0
    return s


def test_an_attachment_the_browser_eval_times_out_on_is_streamed_with_the_session_cookies(
        tmp_path, server):
    # Given: a webmail attachment with no size in its link text, and an eval that hits the CDP limit.
    s = _scraper(tmp_path, _SlowEvalBrowser("✗ CDP command timed out: Runtime.evaluate"))
    # When
    saved = s._download_attachment("10192857", "2. a.xlsx", server, declared_size=None)
    # Then: the whole file lands on disk, fetched once with only the webmail's cookies.
    assert saved.local_path.read_bytes() == BODY
    assert saved.size == len(BODY)
    assert [h.get("Cookie") for h in _Handler.seen] == ["JSESSIONID=s1"]
    assert not list((tmp_path / "att" / "10192857").glob("*.part"))


def test_a_browser_error_that_is_not_a_timeout_still_fails_the_attachment(tmp_path, server):
    # Given: the in-page fetch itself answered with an HTTP error (not a slow transfer).
    s = _scraper(tmp_path, _SlowEvalBrowser("✗ Error: HTTP 404"))
    # When / Then: the error is recorded as before and nothing is fetched behind the browser.
    with pytest.raises(BrowserError, match="HTTP 404"):
        s._download_attachment("10192857", "2. a.xlsx", server, declared_size=None)
    assert _Handler.seen == []

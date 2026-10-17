"""Discord 429 on the calendar confirm transport is waited out, not turned into a failed tick.

2026-10-17 node evidence: `calendar-confirm-watch` failed 18 of 145 one-minute ticks, every one
`http_status=429 retry_after=5` on the confirmation-message read, and doctor reported the watcher
as "최근 실패·중지". These cases drive the real `calendar_confirm._api` (urllib) against a loopback
server; only the HTTP endpoint and the sleep are substituted. A new file because the neighbouring
calendar test files are pinned by FS3 replay hashes (tests/AGENTS.md).
"""
from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import import_module
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "calendar" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

calendar_confirm = import_module("calendar_confirm")
_REAL_API = calendar_confirm._api


class _Discord:
    def __init__(self, limited: int, retry_after: str = "5") -> None:
        self.limited = limited
        self.retry_after = retry_after
        self.calls: list[str] = []


@pytest.fixture
def discord(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_Discord, list[float]]]:
    state = _Discord(limited=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server dispatch name
            state.calls.append(self.path)
            if len(state.calls) <= state.limited:
                body = json.dumps({"retry_after": float(state.retry_after), "global": False}).encode()
                self.send_response(429)
                self.send_header("Retry-After", state.retry_after)
            else:
                body = json.dumps({"id": "m1", "content": "card"}).encode()
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            _ = self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    waits: list[float] = []
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        monkeypatch.setattr(calendar_confirm, "API", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setattr(calendar_confirm, "_api", _REAL_API)
        monkeypatch.setattr(calendar_confirm, "_sleep", waits.append, raising=False)
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "offline-calendar-fixture")
        try:
            yield state, waits
        finally:
            server.shutdown()
            worker.join(timeout=5)


def test_one_rate_limit_is_waited_out_and_the_read_succeeds(discord) -> None:
    # Given: Discord rate-limits the first read with Retry-After 5 (the node's exact answer)
    state, waits = discord
    state.limited = 1
    # When
    message = calendar_confirm._api("GET", "/channels/c/messages/m1")
    # Then: the same read is repeated once after the advertised wait
    assert message == {"id": "m1", "content": "card"}
    assert waits == [5.0]
    assert len(state.calls) == 2


def test_a_persistent_rate_limit_still_fails_after_the_attempt_cap(discord) -> None:
    # Given: every call is rate-limited
    state, waits = discord
    state.limited = 99
    # When / Then: the tick does not spin — it fails like before once the cap is spent
    with pytest.raises(HTTPError) as caught:
        calendar_confirm._api("GET", "/channels/c/messages/m1")
    assert caught.value.code == 429
    assert len(state.calls) == calendar_confirm.API_MAX_ATTEMPTS
    assert waits == [5.0] * (calendar_confirm.API_MAX_ATTEMPTS - 1)


def test_a_retry_after_longer_than_one_tick_is_not_waited(discord) -> None:
    # Given: Discord asks for a wait that would overrun the one-minute tick
    state, waits = discord
    state.limited = 1
    state.retry_after = "30"
    # When / Then: no sleep, the 429 surfaces at once
    with pytest.raises(HTTPError):
        calendar_confirm._api("GET", "/channels/c/messages/m1")
    assert waits == []
    assert len(state.calls) == 1

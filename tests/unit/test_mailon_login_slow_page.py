"""A slow mailon login page still logs in on the first attempt, and submits exactly once.

2026-10-19 (owner: "이 문제 해결하라고"): an approved reply failed at 10:47 KST because the login
page stayed busy and the first `fill` hit agent-browser's 30 s CDP limit; it only went out on the
10-minute retry. Node logs for October show `wait --load domcontentloaded` timing out at 25 s on
every login, so it never proved the form was usable. The fake page below is busy for 25 s or 40 s:
until then each fill times out after 30 s, exactly as on the node. New file because these are new
checks beside the open-timeout cases in test_mailon_login_open_timeout.py.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"
if "pyotp" not in sys.modules:
    sys.modules["pyotp"] = types.SimpleNamespace(TOTP=lambda *_a, **_k: None)  # type: ignore[assignment]
if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *_a, **_k: None)  # type: ignore[assignment]
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon import login as login_mod  # noqa: E402
from mailon.browser import BrowserError  # noqa: E402

_CDP = "agent-browser failed (1): cmd=fill {} stderr=✗ CDP command timed out: Input.insertText"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Page:
    def __init__(self, clock: _Clock, ready_at: float, *, extra_fill_timeouts: int = 0) -> None:
        self.clock, self.ready_at, self.extra = clock, ready_at, extra_fill_timeouts
        self.submits = 0
        self.filled: dict[str, str] = {}
        self.url = "https://mailon.kr/integrated/login"

    def open(self, url: str) -> None:
        self.clock.now += 3.5

    def current_url(self) -> str:
        return self.url

    def wait_load(self, event: str = "networkidle") -> None:
        self.clock.now += 25.0  # measured: never resolved on the node
        raise BrowserError("agent-browser failed (1): cmd=wait --load stderr=✗ Operation timed out")

    def wait_ms(self, ms: int) -> None:
        self.clock.now += ms / 1000

    def find_click(self, *_args, **_kwargs) -> None:
        raise BrowserError("no popup")

    def eval_js(self, js: str) -> str:
        if "login()" in js:
            self.submits += 1
            self.url = "https://mailon.kr/mail/list"
            return '"ok"'
        if self.clock.now < self.ready_at:
            self.clock.now += 30.0
            raise BrowserError("agent-browser failed (1): cmd=eval stderr=✗ CDP command timed out")
        return '"ready"'

    def fill(self, selector: str, value: str) -> None:
        busy = self.clock.now < self.ready_at
        if not busy and self.extra:
            self.extra -= 1
            busy = True
        if busy:
            self.clock.now += 30.0
            raise BrowserError(_CDP.format(selector))
        self.filled[selector] = value


class _Config:
    login_url = "https://mailon.kr/"
    mailon_id = "user@example.org"
    mailon_pw = "pw"
    totp_secret = "secret"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(login_mod, "time", fake)
    monkeypatch.setattr(login_mod, "seconds_until_next_code", lambda: 25)
    monkeypatch.setattr(login_mod, "generate_code", lambda _secret: "123456")
    return fake


@pytest.mark.parametrize("ready_at", [25.0, 40.0])
def test_a_slow_login_page_logs_in_on_the_first_attempt_and_submits_once(
    clock: _Clock, ready_at: float,
) -> None:
    page = _Page(clock, ready_at)

    login_mod.login(page, _Config())  # type: ignore[arg-type]

    assert page.submits == 1
    assert set(page.filled) == {'input[name="ipt-id"]', 'input[name="ipt-pw"]', 'input[name="ipt-otp"]'}
    assert clock.now < login_mod._FORM_READY_SECONDS + 35


def test_a_fill_that_times_out_after_the_form_appears_is_retried_before_submitting(clock: _Clock) -> None:
    page = _Page(clock, 0.0, extra_fill_timeouts=1)

    login_mod.login(page, _Config())  # type: ignore[arg-type]

    assert page.submits == 1


def test_a_page_that_never_becomes_usable_fails_without_submitting(clock: _Clock) -> None:
    page = _Page(clock, 10_000.0)

    with pytest.raises(login_mod.LoginError):
        login_mod.login(page, _Config())  # type: ignore[arg-type]
    assert page.submits == 0


def test_a_non_timeout_fill_error_is_not_retried(clock: _Clock) -> None:
    page = _Page(clock, 0.0)

    def broken(selector: str, value: str) -> None:
        raise BrowserError("agent-browser failed (1): cmd=fill stderr=element not found")

    page.fill = broken  # type: ignore[method-assign]
    with pytest.raises(BrowserError, match="element not found"):
        login_mod.login(page, _Config())  # type: ignore[arg-type]
    assert page.submits == 0

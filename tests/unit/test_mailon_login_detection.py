"""mailon 로그인 성공 판정 회귀 — 거부된 로그인을 성공으로 읽지 않는다.

2026-08-18 실측 배경: `login()` 은 `wait_url("**/mail**")` 로 로그인 완료를
판정하고 URL 검증을 `except` 분기에만 두었다. 그 glob 은 **호스트**에
매칭된다 — `mailon.kr` 자체가 "mail" 을 담고 있어 대기가 32ms 만에
성공 반환했고, 검증 분기는 한 번도 실행되지 않았다. 그래서 자격증명이
거부돼도 "login succeeded" 로 기록됐고, 진짜 실패는 다음 페이지 조작에서
`BrowserError` 로 터져 exit 2(`auth_or_browser_error`)로 뭉뚱그려졌다.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"


def _install_third_party_stubs() -> None:
    """vendor 트리는 스코프 venv 에 pyotp/dotenv 를 두므로 메인 환경엔 없다."""
    if "pyotp" not in sys.modules:
        pyotp = types.ModuleType("pyotp")
        pyotp.TOTP = lambda *_a, **_k: None  # type: ignore[attr-defined]
        sys.modules["pyotp"] = pyotp
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *_a, **_k: None  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv


_install_third_party_stubs()
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon import login as login_mod  # noqa: E402
from mailon.login import LoginError  # noqa: E402

_LOGIN_URL = "https://mailon.kr/integrated/login"
_MAILBOX_URL = "https://mailon.kr/mail/list"

# 대기 예산을 실제 벽시계로 태우지 않는다 — `wait_ms` 가 이 시계를 민다.
_CLOCK = {"now": 0.0}


class _FakeConfig:
    mailon_id = "someone@example.invalid"
    mailon_pw = "dummy"
    totp_secret = "AAAAAAAAAAAAAAAA"
    login_url = _LOGIN_URL


class _FakeBrowser:
    """`login()` 이 실제로 호출하는 표면만 구현한다."""

    def __init__(self, urls: list[str]) -> None:
        self._urls = urls
        self.url_reads = 0
        self.waited_ms = 0
        self.filled: list[str] = []

    # --- navigation -----------------------------------------------------
    def open(self, url: str) -> None:
        self.opened = url

    def wait_load(self, _event: str) -> None:
        return None

    def current_url(self) -> str:
        self.url_reads += 1
        index = min(self.url_reads - 1, len(self._urls) - 1)
        return self._urls[index]

    def wait_ms(self, milliseconds: int) -> None:
        self.waited_ms += milliseconds
        _CLOCK["now"] += milliseconds / 1000.0

    # --- interaction ----------------------------------------------------
    def find_click(self, *_a, **_k) -> None:
        return None

    def fill(self, selector: str, _value: str) -> None:
        self.filled.append(selector)

    def eval_js(self, script: str) -> str:
        if "ipt-otp" in script:  # the login form readiness check
            return '"ready"'
        return "ok"

    def eval_json(self, _script: str):
        return "Sign in | Reset password | Reset OTP"


@pytest.fixture(autouse=True)
def _deterministic_login_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _CLOCK["now"] = 0.0
    monkeypatch.setattr(login_mod, "generate_code", lambda _secret: "123456")
    monkeypatch.setattr(login_mod, "seconds_until_next_code", lambda: 30)
    monkeypatch.setattr(login_mod.time, "monotonic", lambda: _CLOCK["now"])


def test_login_page_predicate_matches_path_not_host() -> None:
    # Given / When / Then: 호스트가 "mail" 을 담고 있어도 메일함으로 읽지 않는다.
    assert login_mod._on_login_page(_LOGIN_URL) is True
    assert login_mod._on_login_page(_MAILBOX_URL) is False


def test_rejected_login_raises_instead_of_reporting_success() -> None:
    # Given: 제출 후에도 계속 로그인 페이지에 머무는 브라우저(자격증명 거부).
    browser = _FakeBrowser([_LOGIN_URL])

    # When / Then: 성공으로 넘어가지 않고 LoginError 로 실패한다.
    with pytest.raises(LoginError) as excinfo:
        login_mod.login(browser, _FakeConfig())

    assert "/integrated/login" in str(excinfo.value)
    # 즉시 포기하지 않고 25초 예산을 실제로 소진했다.
    assert browser.waited_ms > 0
    assert _CLOCK["now"] >= 25.0


def test_login_succeeds_once_the_page_leaves_the_login_path() -> None:
    # Given: 두 번째 조회에서 메일함으로 이동하는 브라우저.
    browser = _FakeBrowser([_LOGIN_URL, _MAILBOX_URL])

    # When: 로그인을 수행한다.
    login_mod.login(browser, _FakeConfig())

    # Then: 예외 없이 통과하고 세 필드를 모두 채웠으며, 예산을 다 쓰지 않았다.
    assert browser.filled == [
        'input[name="ipt-id"]',
        'input[name="ipt-pw"]',
        'input[name="ipt-otp"]',
    ]
    assert _CLOCK["now"] < 25.0


def test_host_matching_glob_is_not_used_for_login_completion() -> None:
    # Given: 판정 로직의 원문.
    source = (_VENDOR / "mailon" / "login.py").read_text(encoding="utf-8")

    # Then: 완료 판정을 URL glob 대기로 되돌리지 않는다. 문자열이 아니라
    # **호출**을 본다 — 헬퍼 docstring 이 옛 glob 을 설명으로 인용하므로
    # 단순 부분문자열 검사는 산문에 걸려 계약을 검증하지 못한다.
    assert "browser.wait_url(" not in source

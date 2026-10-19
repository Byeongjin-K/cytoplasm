"""mailon 브라우저 세션 격리 · 로그인 제출 확인 · 실제 발송 단일 비행 회귀.

2026-09-07 실측 배경: 소유자가 ✅ 한 기관메일이 auth_error(인증 단계 실패, 자동 재시도 불가)로
막혔다. 원인은 **세션 만료가 아니었다** — 프로덕션 노드 실측에서 agent-browser 세션은 close 와
함께 /tmp 프로필째 삭제되고, 같은 이름으로 다시 열어도 document.cookie 가 비어 있다. 즉 모든
호출은 이미 쿠키 0에서 진짜 로그인을 하고 있었다.

진짜 원인은 고정 세션 이름 "mailon-sync-send" 를 **두 프로세스가 공유**한 것이다. agent-browser
는 --session 이름으로 브라우저 인스턴스를 잡으므로 같은 이름의 두 프로세스는 같은 탭을 조작하고,
한쪽의 open/close 가 다른 쪽의 로그인 폼을 갈아치운다(13:06:11.498 / 13:06:15.293 두 프로세스가
로그인 시작 → 13:09:05.965 / 13:09:06.017 두 번의 "submitting login form" → 어느 쪽도 로그인
페이지를 벗어나지 못해 LoginError). 2026-07-20 의 send↔sync 충돌과 같은 계열이며, 그때의 수리
(-send 접미어)는 send↔send 를 막지 못했다.

여기서 고정하는 계약:
  1. 브라우저 팩토리는 호출마다 다른 세션 이름을 만든다 — 한 프로세스 ↔ 한 브라우저.
  2. 우리가 만든 임시 세션의 <name>.config 잔여물은 close 가 치운다(남의 것은 건드리지 않는다).
  3. 로그인 제출 JS 가 실행되지 못했으면(no-login-fn) 25초를 침묵으로 태우지 않고 즉시 실패한다.
  4. 실제 발송(dry-run 아님)은 크로스 프로세스 lock 으로 직렬화된다 — 발송 전 중복검사는
     check-then-act 라 실측에서 두 프로세스가 65ms 차로 나란히 통과했다(이중발송 위험).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"


class _AnyModule(types.ModuleType):
    """이름이 무엇이든 받아 주는 스텁 — from X import Y 형태까지 견딘다."""

    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


def _install_third_party_stubs() -> None:
    """vendor 트리는 스코프 venv 에 서드파티를 두므로 메인 환경엔 없다."""
    for name in ("pyotp", "dotenv", "bs4", "lxml"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = _AnyModule(name)


_install_third_party_stubs()
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon import login as login_mod  # noqa: E402
from mailon.browser import AgentBrowser  # noqa: E402
from mailon.login import LoginError  # noqa: E402

_LOGIN_URL = "https://mailon.kr/integrated/login"
_MAILBOX_URL = "https://mailon.kr/mail/list"
_CLOCK = {"now": 0.0}


class _Cfg:
    session_name = "mailon-sync"
    headless = True
    mailon_id = "someone@example.invalid"
    mailon_pw = "dummy"
    totp_secret = "AAAAAAAAAAAAAAAA"
    login_url = _LOGIN_URL


# ------------------------------------------------------- 1. 세션 격리


def test_send_browser_session_is_unique_per_invocation() -> None:
    # Given: 같은 설정으로 두 번 브라우저를 만든다(= 동시에 도는 두 프로세스).
    from mailon.main import _make_send_browser

    first = _make_send_browser(_Cfg())
    second = _make_send_browser(_Cfg())

    # Then: 세션 이름이 달라야 서로의 탭·브라우저를 죽일 수 없다.
    assert first.session_name != second.session_name
    # 그리고 로그에서 어느 명령의 세션인지 읽히도록 접두어는 유지한다.
    assert first.session_name.startswith("mailon-sync-send")
    assert second.session_name.startswith("mailon-sync-send")


def test_every_browser_factory_is_process_isolated() -> None:
    # Given / When: sync·send·resolve 세 팩토리 모두.
    from mailon.main import _make_browser, _make_resolve_browser, _make_send_browser

    made = [
        factory(_Cfg())
        for factory in (_make_browser, _make_send_browser, _make_resolve_browser)
    ]

    # Then: 셋 다 고유하고, 우리가 만든 임시 세션임을 스스로 안다.
    assert len({browser.session_name for browser in made}) == 3
    assert all(browser.ephemeral for browser in made)


def test_close_discards_only_the_session_config_it_minted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 세션 이름당 하나씩 생기는 ~/.agent-browser/<name>.config (노드 실측).
    from mailon.browser import unique_session_name

    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".agent-browser"
    config_dir.mkdir()
    name = unique_session_name("mailon-sync-send")
    mine = config_dir / f"{name}.config"
    mine.write_text("x", encoding="utf-8")
    foreign = config_dir / "mailon-sync.config"
    foreign.write_text("x", encoding="utf-8")

    browser = AgentBrowser(session_name=name, headless=True, ephemeral=True)
    monkeypatch.setattr(browser, "_run", lambda *_a, **_k: "")

    # When: 닫는다.
    browser.close()

    # Then: 내가 만든 잔여물만 사라지고 남의 세션 파일은 그대로다.
    assert not mine.exists()
    assert foreign.exists()


# ------------------------------------------------ 2. 로그인 제출 확인


class _FakeBrowser:
    """login() 이 실제로 호출하는 표면만 구현한다."""

    def __init__(self, urls: list[str], submit_result: str = '"ok"') -> None:
        self._urls = urls
        self._submit_result = submit_result
        self.opened = ""
        self.url_reads = 0
        self.waited_ms = 0
        self.filled: list[str] = []

    def open(self, url: str) -> None:
        self.opened = url

    def wait_load(self, _event: str) -> None:
        return None

    def current_url(self) -> str:
        self.url_reads += 1
        return self._urls[min(self.url_reads - 1, len(self._urls) - 1)]

    def wait_ms(self, milliseconds: int) -> None:
        self.waited_ms += milliseconds
        _CLOCK["now"] += milliseconds / 1000.0

    def find_click(self, *_a, **_k) -> None:
        return None

    def fill(self, selector: str, _value: str) -> None:
        self.filled.append(selector)

    def eval_js(self, script: str) -> str:
        if "ipt-otp" in script:  # the login form readiness check
            return '"ready"'
        return self._submit_result

    def eval_json(self, _script: str):
        return "Sign in | Reset password"


@pytest.fixture(autouse=True)
def _deterministic_login_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _CLOCK["now"] = 0.0
    monkeypatch.setattr(login_mod, "generate_code", lambda _secret: "123456")
    monkeypatch.setattr(login_mod, "seconds_until_next_code", lambda: 30)
    monkeypatch.setattr(login_mod.time, "monotonic", lambda: _CLOCK["now"])


def test_login_fails_immediately_when_the_submit_function_is_absent() -> None:
    # Given: 페이지가 login() 을 노출하지 않아 제출이 no-op 인 브라우저.
    browser = _FakeBrowser([_LOGIN_URL], submit_result='"no-login-fn"')

    # When / Then: 25초 폴링을 태우지 않고 제출 실패를 이름 그대로 말한다.
    with pytest.raises(LoginError) as excinfo:
        login_mod.login(browser, _Cfg())

    message = str(excinfo.value)
    assert "no-login-fn" in message or "submit" in message.lower()
    assert _CLOCK["now"] < 25.0, "제출조차 못 한 실패를 25초 침묵으로 감추면 안 된다"


def test_login_accepts_the_quoted_ok_the_cli_actually_prints() -> None:
    # Given: agent-browser 는 eval 결과를 JSON 따옴표로 감싸 출력한다(노드 실측:
    # eval "document.cookie" 가 "ulwprobe=1" 을 따옴표째 찍었다). 제출 확인을 순진하게
    # == "ok" 로 짜면 정상 로그인이 전부 막힌다 — 이 테스트가 그 과잉 엄격을 막는다.
    browser = _FakeBrowser([_LOGIN_URL, _MAILBOX_URL], submit_result='"ok"\n')

    # When: 로그인한다.
    login_mod.login(browser, _Cfg())

    # Then: 예외 없이 통과하고 세 필드를 모두 채웠다.
    assert browser.filled == [
        'input[name="ipt-id"]',
        'input[name="ipt-pw"]',
        'input[name="ipt-otp"]',
    ]


# --------------------------------------------------- 3. 발송 단일 비행 lock


def test_send_lock_is_exclusive_across_processes(tmp_path: Path) -> None:
    # Given: 같은 lock 파일을 두 주체가 잡으려 한다(별도 fd = 별도 프로세스와 같은 의미).
    from mailon import send_lock

    path = tmp_path / "send.lock"
    slept: list[float] = []

    with send_lock.hold(path):
        # When / Then: 두 번째는 기다리다 시간이 다하면 fail-closed 로 거절한다.
        with pytest.raises(send_lock.SendLockBusy):
            with send_lock.hold(
                path,
                timeout_s=3.0,
                poll_s=1.0,
                sleep=slept.append,
                clock=lambda: float(len(slept)),
            ):
                pytest.fail("이미 잡힌 lock 을 두 번째가 잡아서는 안 된다")

    # And: 앞선 발송이 끝나면 다음 발송은 곧바로 들어온다(영구 차단이 아니다).
    with send_lock.hold(path, timeout_s=1.0):
        pass


def test_real_send_takes_the_lock_and_dry_run_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 발송 경로를 브라우저 없이 돌리는 최소 대역.
    from mailon import main as main_mod
    from mailon.send import SendResult

    entered: list[str] = []

    class _Recorder:
        SendLockBusy = RuntimeError

        @staticmethod
        def lock_path(data_dir: Path) -> Path:
            return Path(data_dir) / "send.lock"

        class hold:
            def __init__(self, path, **_kwargs) -> None:
                self._path = path

            def __enter__(self):
                entered.append(str(self._path))
                return None

            def __exit__(self, *_exc) -> bool:
                return False

    class _Cfg2:
        session_name = "mailon-sync"
        headless = True
        logs_dir = tmp_path / "logs"
        data_dir = tmp_path / "data"

    class _Browser:
        session_name = "stub"

        def clear_network_requests(self) -> None:
            return None

        def close(self) -> None:
            return None

    class _Sender:
        def __init__(self, _browser) -> None:
            return None

        def send(self, _request, *, dry_run: bool) -> SendResult:
            return SendResult(
                status="dry_run" if dry_run else "submitted",
                csrf_present=True,
                attachment_count=0,
                network_post_count=0,
                verified=not dry_run,
            )

    monkeypatch.setattr(main_mod, "send_lock", _Recorder, raising=False)
    monkeypatch.setattr(main_mod, "load_config", lambda: _Cfg2())
    monkeypatch.setattr(main_mod, "_setup_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(main_mod, "_make_send_browser", lambda _cfg: _Browser())
    monkeypatch.setattr(main_mod, "login", lambda *_a, **_k: None)
    monkeypatch.setattr(main_mod, "ComposeSender", _Sender)
    monkeypatch.setattr(main_mod, "record_send_result", lambda *_a, **_k: None)

    base = ["send", "--to", "a@example.invalid", "--subject", "s", "--body", "b", "--json"]
    parser = main_mod.build_parser()

    # When: dry-run 발송.
    assert main_mod.cmd_send(parser.parse_args([*base, "--dry-run"])) == 0
    # Then: 외부효과가 없으므로 직렬화하지 않는다.
    assert entered == []

    # When: 실제 발송.
    assert main_mod.cmd_send(parser.parse_args([*base, "--confirm-send"])) == 0
    # Then: 되돌릴 수 없는 그 한 걸음만 lock 안에서 일어난다.
    assert entered == [str(_Cfg2.data_dir / "send.lock")]

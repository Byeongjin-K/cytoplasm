"""편집기 iframe 이 다시 로드되는 동안의 본문 채우기는 재시도된다 — 회귀 고정.

2026-09-28 실측 배경: compose 를 연 뒤 1초 간격으로 Namo 편집기 iframe
(`#NamoSE_Ifr__mail_editor_nm`)을 보면, body 가 t+1.4s 에 생겼다가
**t+2.5s~5.9s 동안 사라지고**(문서는 있으나 body 없음 — iframe 이 스스로 다시
로드된다) t+7.8s 부터 안정된다. 발송 경로는 고정 3초를 기다린 뒤 채우기를 한 번만
시도했으므로 정확히 그 구간에 걸려 `compose editor frame unavailable` 을 던졌고,
mailon 은 그것을 `external_service_error`(stage=provider)로 접었다. 워처가 2분마다
재시도해도 같은 구간에 걸려 승인된 메일 두 건이 각각 수 시간 동안 나가지 못했다
(약 150회 중 1회 통과).

`open_compose_when_ready` 와 같은 원리로, 준비 상태를 술어로 판정하지 않고 채우기
호출 자체를 재시도해 그 성공을 준비 신호로 삼는다. 다른 실패는 재시도하지 않는다.

`test_mailon_compose_readiness.py` 에 붙이지 않고 새 파일로 둔 이유: 그 파일은
compose 기동 경쟁을 고정하고, 이것은 compose 가 열린 **뒤** 편집기의 재로드다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon.browser import BrowserError  # noqa: E402
from mailon.send import ComposeSender, SendRequest  # noqa: E402

_FRAME_GONE = (
    "agent-browser failed (1): cmd=eval --stdin stderr=✗ Evaluation error: "
    "Error: compose editor frame unavailable"
)


class _ReloadingEditorBrowser:
    """compose 는 바로 열리고, 편집기 채우기는 `unavailable_s` 초 동안 실패한다."""

    def __init__(self, unavailable_s: float, *, error: str = _FRAME_GONE) -> None:
        self.now = 0.0
        self._unavailable_until = unavailable_s
        self._error = error
        self.fill_attempts = 0

    def clock(self) -> float:
        return self.now

    def eval_js(self, script: str) -> str:
        if "NamoSE_Ifr__mail_editor_nm" in script:
            self.fill_attempts += 1
            if self.now < self._unavailable_until:
                raise BrowserError(self._error)
        return '""'

    def eval_json(self, _script: str):
        return {"csrf": {"name": "t", "value": "v"}, "file_input": None}

    def wait_ms(self, milliseconds: int) -> None:
        self.now += milliseconds / 1000.0

    def clear_network_requests(self) -> None:
        return None

    def network_post_count(self) -> int:
        return 0

    def network_requests(self) -> str:
        return "[]"

    def fill(self, _selector: str, _value: str) -> None:
        return None

    def upload(self, _selector: str, _paths: tuple[Path, ...]) -> None:
        return None


def _request() -> SendRequest:
    return SendRequest(
        recipients=("owner@example.invalid",),
        cc=(),
        subject="s",
        body="b",
        attachments=(),
    )


def test_fill_waits_out_the_measured_editor_reload() -> None:
    # Given: 실측처럼 t+7.8s 까지 편집기 body 가 없는 compose.
    browser = _ReloadingEditorBrowser(unavailable_s=7.8)

    # When: dry-run 으로 본문까지 채운다.
    result = ComposeSender(browser, clock=browser.clock).send(_request(), dry_run=True)

    # Then: 재로드가 끝난 뒤의 채우기가 성공하고, 한 번에 포기하지 않았다.
    assert result.status == "dry_run"
    assert browser.fill_attempts > 1
    assert browser.now >= 7.8


def test_fill_still_fails_closed_when_the_editor_never_returns() -> None:
    # Given: 편집기가 끝내 돌아오지 않는 compose.
    browser = _ReloadingEditorBrowser(unavailable_s=float("inf"))

    # When / Then: 예산을 다 쓴 뒤 원래 오류 그대로 실패한다(무한 대기 없음).
    with pytest.raises(BrowserError, match="compose editor frame unavailable"):
        ComposeSender(browser, clock=browser.clock).send(_request(), dry_run=True)
    assert browser.fill_attempts > 1
    assert browser.now < 90.0


def test_fill_waits_out_a_slow_page_whose_editor_returns_after_twenty_seconds() -> None:
    # Given: 2026-10-19 실측처럼 느린 페이지에서 편집기가 20초 넘게 돌아오지 않는 compose.
    browser = _ReloadingEditorBrowser(unavailable_s=25.0)

    # When: dry-run 으로 본문까지 채운다.
    result = ComposeSender(browser, clock=browser.clock).send(_request(), dry_run=True)

    # Then: 편집기가 돌아온 뒤의 채우기가 성공한다.
    assert result.status == "dry_run"
    assert browser.now >= 25.0


def test_other_editor_errors_are_not_retried() -> None:
    # Given: 편집기 재로드가 아닌 다른 브라우저 실패.
    browser = _ReloadingEditorBrowser(
        unavailable_s=float("inf"), error="agent-browser failed (1): daemon gone"
    )

    # When / Then: 즉시 그대로 전파된다 — 재시도는 재로드 구간에만 쓴다.
    with pytest.raises(BrowserError, match="daemon gone"):
        ComposeSender(browser, clock=browser.clock).send(_request(), dry_run=True)
    assert browser.fill_attempts == 1

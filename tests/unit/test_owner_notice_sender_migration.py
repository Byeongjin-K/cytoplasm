"""ON-3 sender migration coverage kept separate from FS3-pinned DM inventory tests.

Each sender delegates destination selection to ``automation.owner_notice``: a
configured notice channel is attempted; when absent, the facade's DM fallback
is attempted instead. The test stubs only the final transport.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from automation import owner_notice
from automation.interop import gate_driver
from automation.interop.hermes_plugin import _send_direct_result
from automation.memory_curator.effects import alert_owner

Sender = Callable[[str], object]
_INTEROP_RECEIPT = (
    "대상: 에이전트 위임 응답 (corr-1)\n"
    "사실: 위임 응답 수신 (실행 완료)\n"
    "위치: 링크 없음 (공간 미상); 검색: Discord 검색 / corr-1\n"
    "인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n"
    "되돌리기: 해당 없음"
)

_CURATOR_BODY = (
    "대상: 메모리 큐레이터 점검 (memory-curator)\n"
    "사실: near-cap body (실행 완료)\n"
    "위치: 해당 없음\n"
    "인계: 소유자: 조치 없음; 다음: 다음 주기에 메모리 상태를 다시 확인합니다.\n"
    "되돌리기: 해당 없음"
)


def _interop_sender(correlation_id: str) -> None:
    _send_direct_result(correlation_id)


def _curator_sender(body: str) -> bool:
    return alert_owner(body)


def _configured_channel(_home: Path | None = None) -> str:
    return "notice-1"


def _absent_channel(_home: Path | None = None) -> str:
    return ""


def _owner_id() -> str:
    return "owner-1"


def _owner_dm_channel(_token: str, _owner: str) -> str:
    return "owner-dm"


@pytest.fixture
def notice_attempts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    attempts: list[tuple[str, str]] = []

    def capture_send(_token: str, channel_id: str, body: str) -> None:
        attempts.append((channel_id, body))

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    monkeypatch.setattr(owner_notice, "_config_owner_id", _owner_id)
    monkeypatch.setattr(owner_notice, "owner_dm_channel", _owner_dm_channel)
    monkeypatch.setattr(owner_notice, "send_notice", capture_send)
    return attempts


@pytest.mark.parametrize(
    ("sender", "input_body", "delivered_body"),
    (
        (_interop_sender, "corr-1", _INTEROP_RECEIPT),
        (_curator_sender, "near-cap body", _CURATOR_BODY),
    ),
)
def test_notice_senders_attempt_configured_channel(
    monkeypatch: pytest.MonkeyPatch,
    notice_attempts: list[tuple[str, str]],
    sender: Sender,
    input_body: str,
    delivered_body: str,
) -> None:
    monkeypatch.setattr(owner_notice, "owner_notice_channel", _configured_channel)

    _ = sender(input_body)

    assert notice_attempts == [("notice-1", delivered_body)]


@pytest.mark.parametrize(
    ("sender", "input_body", "delivered_body"),
    (
        (_interop_sender, "corr-1", _INTEROP_RECEIPT),
        (_curator_sender, "near-cap body", _CURATOR_BODY),
    ),
)
def test_notice_senders_fall_back_to_owner_dm_when_channel_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    notice_attempts: list[tuple[str, str]],
    sender: Sender,
    input_body: str,
    delivered_body: str,
) -> None:
    monkeypatch.setattr(owner_notice, "owner_notice_channel", _absent_channel)

    _ = sender(input_body)

    assert notice_attempts == [("owner-dm", delivered_body)]


def test_gate_driver_uses_facade_notice_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(owner_notice, "owner_notice_channel", _configured_channel)

    assert gate_driver._result_notice_target("test-token") == "notice-1"


def test_gate_driver_falls_back_to_facade_owner_dm_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(owner_notice, "owner_notice_channel", _absent_channel)
    monkeypatch.setattr(owner_notice, "_config_owner_id", _owner_id)
    monkeypatch.setattr(owner_notice, "owner_dm_channel", _owner_dm_channel)

    assert gate_driver._result_notice_target("test-token") == "owner-dm"

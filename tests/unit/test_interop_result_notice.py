"""Protocol receipt values survive retirement of the Hermes delivery hook."""
from __future__ import annotations

import pytest

from automation.interop.delegation import InteropEnvelope, result_message
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result


@pytest.mark.parametrize(("intent", "subject", "fact"), [
    ("response_confirm_slot", "에이전트 일정 확인", "peer-test 응답 수신 · response_confirm_slot"),
    ("response_custom", "에이전트 위임 응답", "peer-test 응답 수신 · response_custom"),
])
def test_fields_when_peer_declines(intent: str, subject: str, fact: str) -> None:
    # Given: declined responses are receipts, not evidence of calendar execution.
    envelope = InteropEnvelope("corr-1", "peer-test", "agent-test", intent, {"result": "declined"})
    # When: a receipt is built without Discord coordinates.
    message = result_message("corr-1", envelope, None)
    # Then: all fields retain the receipt semantics and the known search key.
    assert message == OwnerMessage(
        subject_key="corr-1", subject=subject, fact=fact,
        location=Ref(scope="channel", search=("Discord 검색", "corr-1")),
        owner=Action(verb="none"), agent_next="추가 실행 없음",
        recovery="not_applicable", detail=Result(outcome="executed"),
    )


def test_fields_when_only_correlation_is_available() -> None:
    # Given / When: a legacy internal caller has only the protocol correlation.
    message = result_message("corr-1", None, None)
    # Then: no coordinates or completion details are invented.
    assert message == OwnerMessage(
        subject_key="corr-1", subject="에이전트 위임 응답", fact="위임 응답 수신",
        location=Ref(scope="channel", search=("Discord 검색", "corr-1")),
        owner=Action(verb="none"), agent_next="추가 실행 없음",
        recovery="not_applicable", detail=Result(outcome="executed"),
    )

"""K3 thread lifecycle regressions, separate from the recorded v4 card tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from automation.interop import origin_notice
from automation.interop.approval_directory import JsonValue
from tests.unit import test_mail_single_live_request as mail
from tests.unit.test_approval_directory import RecordedCall
from tests.unit.test_mail_card_v4_supersede import THREAD, _compose, _post
from tests.unit.test_mail_single_live_request import mail_env as mail_env

@pytest.fixture
def thread_calls(
    mail_env: tuple[mail.FakeDiscord, list[str], Path], monkeypatch: pytest.MonkeyPatch,
) -> list[RecordedCall]:
    fake = mail_env[0]
    fake.threads[THREAD] = "mail request"
    calls: list[RecordedCall] = []
    api = fake.api

    def capture(
        method: str, path: str, payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        calls.append((method, path, payload))
        if method == "PATCH":
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                assert payload is not None
                fake.contents[parts[3]] = str(payload["content"])
            return {"id": parts[1]}
        return api(method, path, payload)

    monkeypatch.setattr(mail.triage_confirm, "_api", capture)
    monkeypatch.setattr(mail.triage_confirm, "_origin_notice", lambda: origin_notice)
    # Keep the real result delivery and card-reply payloads, replacing only network I/O.
    monkeypatch.setattr(
        mail.triage_confirm, "_dm_transport",
        lambda channel: mail.triage_confirm._CardReply(channel, ""),
    )
    return calls


@pytest.mark.parametrize("outcome", ["DONE", "CANCELLED", "EXPIRED"])
def test_compose_result_closes_reused_owner_thread_after_post(
    thread_calls: list[RecordedCall], outcome: str,
) -> None:
    # Given: compose uses the owner's existing thread, just like a digest reply structurally.
    draft = _post(_compose("send body"))
    thread_calls.clear()
    # When: this request reaches a terminal outcome.
    mail.triage_confirm.notify_result(draft, "result", outcome=outcome)
    # Then: the result is not a card reply, and the request thread closes after posting it.
    writes = [(method, path, payload) for method, path, payload in thread_calls
              if method in {"POST", "PATCH"}]
    assert [method for method, _, _ in writes] == ["POST", "PATCH"]
    assert writes[0][1] == f"/channels/{THREAD}/messages"
    assert writes[0][2] is not None
    assert "message_reference" not in writes[0][2]
    assert writes[1] == (
        "PATCH", f"/channels/{THREAD}",
        {"archived": True, "name": f"{origin_notice.ThreadOutcome[outcome].value} · mail request"},
    )

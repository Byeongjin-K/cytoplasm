"""Todo result envelopes preserve delivery compatibility and execution claims."""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from functools import partial
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from typing import Final, Literal, assert_never

import pytest

from tests.unit.test_todo_execution_claim import FakeGws, _approved
from tests.unit.test_todo_origin_thread import _FakeTransport, _thread_factory

import todo_approval_runtime as runtime
import todo_cli
import todo_confirm_reaction_watch as watch
from todo_approval_model import ApprovalState, TodoApprovalRecord
from automation.interop import origin_notice, owner_message
from automation.interop.effect_approval import ApprovalContext


LEGACY_BODIES: Final = {
    "DONE": "✅ 할일 등록 완료: 합성 작업 (task task-1)\n소유자 ✅ 승인 · tasks.get 재조회로 검증되었습니다.",
    "CANCELLED": "⛔ 할일 등록 취소 (승인 sha256:fixture) — 소유자 ⛔ 리액션으로 취소되어 Google Tasks에 등록되지 않았습니다.",
    "EXPIRED": "⌛ 할일 등록 승인 만료: 합성 작업 (key todo:sha256:fixture)\\n승인 TTL 86400초가 지나 Google Tasks에 등록되지 않았습니다.",
}


@pytest.fixture
def saved_record() -> TodoApprovalRecord:
    return TodoApprovalRecord(
        key="todo:sha256:fixture", generation=1, action_hash="sha256:fixture", target_id="target",
        argv_summary="masked", message_id="333", created_at=datetime(2026, 9, 1, tzinfo=UTC),
        state=ApprovalState.ARCHIVED, outcome="approved", kind="todo", surface="owner-dm",
        channel_id="444", policy_version=7, approval_thread_id="222", approval_guild_id="111",
        title="합성 작업",
    )


@pytest.fixture(params=["missing-module", "old-signature"])
def legacy_runtime(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    match request.param:
        case "missing-module":
            monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
        case "old-signature":
            original = origin_notice.deliver
            # The old facade's exact keyword interface is the compatibility boundary.
            def deliver(*, api, transport_factory, record, thread_name, content, fallback, outcome=None):
                return original(api=api, transport_factory=transport_factory, record=record,
                                thread_name=thread_name, content=content, fallback=fallback, outcome=outcome)
            monkeypatch.setattr(runtime, "_repo_module", lambda _name: SimpleNamespace(deliver=deliver))


@pytest.mark.usefixtures("legacy_runtime")
@pytest.mark.parametrize("outcome", ["DONE", "CANCELLED", "EXPIRED"])
def test_producer_legacy_bytes_when_runtime_is_older(
    saved_record: TodoApprovalRecord, monkeypatch: pytest.MonkeyPatch,
    outcome: Literal["DONE", "CANCELLED", "EXPIRED"],
) -> None:
    # Given: literals captured from the base producer, not its formatter.
    posts: list[tuple[str, str]] = []
    monkeypatch.setenv("AUTOPHAGY_DEMO_SECRET", "")
    monkeypatch.setenv("TODO_APPROVAL_TTL", "86400")
    monkeypatch.setattr(runtime, "origin_record", lambda _hash: {
        "id": "sha256:fixture", "channel_id": "444", "approval_thread_id": "222",
    })
    transport = _FakeTransport()
    factory = _thread_factory(posts)
    monkeypatch.setattr(runtime, "notify_result", partial(
        runtime.notify_result, transport=transport, transport_factory=factory,
    ))
    # When: the real terminal producer reports its execution outcome.
    match outcome:
        case "DONE":
            todo_cli._notify_created("sha256:fixture", "task-1", "합성 작업", ApprovalContext(None, "111", False))
        case "CANCELLED":
            watch._notify_cancelled(saved_record, transport, transport_factory=factory)
        case "EXPIRED":
            runtime.notify_expired(saved_record)
        case unreachable:
            assert_never(unreachable)
    # Then: old-runtime delivery uses the independently captured bytes.
    assert posts == [("222", LEGACY_BODIES[outcome])]


@pytest.fixture
def record() -> dict[str, str]:
    return {
        "id": "sha256:fixture", "title": "합성 작업", "channel_id": "444",
        "approval_guild_id": "111", "approval_thread_id": "222", "message_id": "333",
        "origin_channel_id": "555", "origin_message_id": "666",
    }


@pytest.mark.parametrize("case", list(product(
    ["missing-module", "old-signature", "render-failure"], ["DONE", "CANCELLED", "EXPIRED", "", "approved"],
)))
def test_preserves_content_bytes_when_runtime_is_older(
    record: dict[str, str], monkeypatch: pytest.MonkeyPatch,
    case: tuple[Literal["missing-module", "old-signature", "render-failure"], str],
) -> None:
    # Given: exact producer bytes and an unavailable envelope capability.
    compatibility, outcome = case
    content = "합성 결과\n(task fixture)  "
    posts: list[tuple[str, str]] = []
    match compatibility:
        case "missing-module":
            monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
        case "old-signature":
            # Signature preservation intentionally mirrors the real facade's keyword API.
            def deliver(*, api, transport_factory, record, thread_name, content, fallback, outcome=None):
                return transport_factory(record["approval_thread_id"]).send(content)[-1].message_id
            monkeypatch.setattr(runtime, "_repo_module", lambda _name: SimpleNamespace(deliver=deliver))
        case "render-failure":
            def reject(_message, *, destination):
                raise owner_message.OwnerMessageError(detail="message.render_version")
            monkeypatch.setattr(owner_message, "render", reject)
        case unreachable:
            assert_never(unreachable)
    # When: the result goes through the actual notify facade adapter.
    result = runtime.notify_result(
        record, content, thread_name="할일", outcome=outcome,
        transport=_FakeTransport(), transport_factory=_thread_factory(posts),
    )
    # Then: fallback bytes and the delivery receipt remain unchanged.
    assert posts == [("222", content)]
    assert result == "thread-post-1"


@pytest.mark.parametrize("started", [False, True])
def test_cli_exit_and_claim_survive_when_notice_transport_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: bool,
) -> None:
    # Given: a real approved generation and a failing Discord boundary.
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(Path(__file__).resolve().parents[2]))
    monkeypatch.delenv("AUTOPHAGY_DEMO_SECRET", raising=False)
    item = _approved(tmp_path)
    monkeypatch.setenv("TODO_APPROVAL_ROOT", str(item.claims.root))
    fake = FakeGws()
    states: list[str] = []

    class FailedTransport(_FakeTransport):
        def post_message(self, channel_id: str, content: str) -> str:
            states.append(item.claims.status(item.record))
            raise RuntimeError("injected notice failure")

    monkeypatch.setattr(runtime, "notify_result", partial(
        runtime.notify_result, transport=FailedTransport(), transport_factory=_thread_factory([]),
    ))
    monkeypatch.setattr(item.todo, "approval_context", lambda: item.context)
    monkeypatch.setattr(item.todo, "run_gws", fake)
    if started:
        decision = item.todo.evaluate(item.todo.insert_argv(item.request), context=item.context)
        item.claims.acquire(decision, item.context)
        item.todo._notify_created(decision.action_hash, "task-1", item.request.title, item.context)
    # When: the legacy approved writer executes (or resumes an uncertain write).
    try:
        item.todo.create_task(item.request)
        result = 0
    except item.todo.TodoError as error:
        result = error.exit_code
    # Then: notice failure cannot change the receipt, exit code, or insert count.
    expected = "write_started" if started else "verified"
    assert (result, item.claims.status(item.record), states, fake.methods) == (
        7 if started else 0, expected, [expected], [] if started else ["insert", "get"],
    )
    print(f"notice-failure exit={result} claim={expected} methods={fake.methods} PASS")

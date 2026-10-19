from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from automation.interop.effect_approval import ApprovalContext


_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "todo" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_OWNER = "owner-fixture"
_MESSAGE = "1530000000000000001"
_NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


@dataclass(slots=True)
class FakeGws:
    mismatch: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, argv: list[str]) -> dict[str, Any]:
        self.calls.append(tuple(argv))
        if argv[3] == "insert":
            body = json.loads(argv[argv.index("--json") + 1])
            return {"id": "task-1", "title": body["title"]}
        title = "다른 제목" if self.mismatch else "승인 과제"
        return {"id": "task-1", "title": title}

    @property
    def methods(self) -> list[str]:
        return [call[3] for call in self.calls]


@dataclass(frozen=True, slots=True)
class ApprovedFixture:
    todo: Any
    request: Any
    context: ApprovalContext
    claims: Any
    record: Any


@pytest.fixture(autouse=True)
def _runtime_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(_REPO))


def _approved(root: Path, *, outcome: str = "approved") -> ApprovedFixture:
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    store_module = import_module("todo_approval_store")
    claims_module = import_module("todo_execution_claim")
    request = todo.TaskRequest("@default", "승인 과제")
    argv = todo.insert_argv(request)
    decision = todo.evaluate(argv, context=ApprovalContext(None, _OWNER, False))
    log = root / "approvals.jsonl"
    log.write_text(json.dumps({
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": _MESSAGE,
            "method": "manual_reaction",
            "owner_id": _OWNER,
        },
        "hash": decision.action_hash,
        "result": {"status": "approved"},
        "target_id": decision.target_id,
        "timestamp": "2026-08-16T12:00:00Z",
    }) + "\n", encoding="utf-8")
    store = store_module.TodoApprovalStore(root / "todo-approvals")
    spec = store_module.TodoApprovalSpec(
        f"todo:{decision.action_hash}", decision.action_hash, decision.target_id,
        approval.masked_argv_summary(argv), "todo", "owner-dm", "owner-dm-1", 7,
    )
    pending = store.bind_message(store.prepare(spec, _NOW), _MESSAGE)
    record = store.archive(pending, store_module.ApprovalState.ARCHIVED, outcome)
    return ApprovedFixture(
        todo,
        request,
        ApprovalContext(log, _OWNER, False),
        claims_module.ApprovalClaimStore(root / "todo-approvals"),
        record,
    )


def test_approved_generation_executes_once_then_replay_calls_nothing(tmp_path: Path) -> None:
    item = _approved(tmp_path)
    first = FakeGws()
    created = item.todo.create_task(
        item.request, runner=first, context=item.context, claim_store=item.claims
    )
    replay = FakeGws()
    with pytest.raises(item.todo.ApprovalRequiredError):
        item.todo.create_task(
            item.request, runner=replay, context=item.context, claim_store=item.claims
        )
    assert first.methods == ["insert", "get"]
    assert replay.calls == []
    assert created.verified is True
    assert item.claims.status(item.record) == "verified"


def test_cancelled_generation_never_reaches_external_runner(tmp_path: Path) -> None:
    item = _approved(tmp_path, outcome="cancelled")
    fake = FakeGws()
    with pytest.raises(item.todo.ApprovalRequiredError):
        item.todo.create_task(
            item.request, runner=fake, context=item.context, claim_store=item.claims
        )
    assert fake.calls == []


def test_write_started_restart_requires_reconciliation_without_reinsert(tmp_path: Path) -> None:
    item = _approved(tmp_path)
    decision = item.todo.evaluate(item.todo.insert_argv(item.request), context=item.context)
    item.claims.acquire(decision, item.context)
    fake = FakeGws()
    with pytest.raises(item.todo.TodoReconciliationRequiredError):
        item.todo.create_task(
            item.request, runner=fake, context=item.context, claim_store=item.claims
        )
    assert fake.calls == []
    assert item.claims.status(item.record) == "write_started"


def test_concurrent_create_allows_exactly_one_insert_and_get(tmp_path: Path) -> None:
    item = _approved(tmp_path)
    barrier = threading.Barrier(3)
    fake = FakeGws()
    successes: list[object] = []
    failures: list[Exception] = []

    def invoke() -> None:
        barrier.wait()
        try:
            successes.append(item.todo.create_task(
                item.request, runner=fake, context=item.context, claim_store=item.claims
            ))
        except Exception as error:
            failures.append(error)

    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert len(successes) == 1
    assert len(failures) == 1
    assert fake.methods == ["insert", "get"]


def test_reread_mismatch_leaves_write_started_without_trusted_receipt(tmp_path: Path) -> None:
    item = _approved(tmp_path)
    fake = FakeGws(mismatch=True)
    with pytest.raises(item.todo.VerificationFailedError):
        item.todo.create_task(
            item.request, runner=fake, context=item.context, claim_store=item.claims
        )
    assert fake.methods == ["insert", "get"]
    assert item.claims.status(item.record) == "write_started"

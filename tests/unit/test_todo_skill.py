"""Legacy task cards drain once and verify the resulting task by re-read.

New owner-internal requests use the cardless owner-command writer, covered by
test_todo_write_now. Hermes tool classification is retired.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from automation.interop import effect_approval as gate
from automation.interop.effect_approval import ApprovalContext

_REPO = Path(__file__).resolve().parents[2]
_SCENARIO = _REPO / "skills" / "todo" / "scripts" / "scenario.sh"
sys.path.insert(0, str(_REPO / "skills" / "todo" / "scripts"))

OWNER = "owner-fixture"


@pytest.fixture
def todo() -> ModuleType:
    return import_module("todo_cli")


@pytest.fixture(autouse=True)
def _runtime_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(_REPO))


def _approve(log: Path, decision: gate.ExternalEffectDecision) -> Any:
    record = {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": "fixture-message",
            "method": "manual_reaction",
            "owner_id": OWNER,
        },
        "hash": decision.action_hash,
        "result": {"status": "approved"},
        "target_id": decision.target_id,
        "timestamp": "2026-07-28T00:00:00Z",
    }
    log.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    store_module = import_module("todo_approval_store")
    store = store_module.TodoApprovalStore(log.parent / "todo-approvals")
    spec = store_module.TodoApprovalSpec(
        f"todo:{decision.action_hash}",
        decision.action_hash,
        decision.target_id,
        "gws tasks tasks insert [masked]",
        "todo",
        "owner-dm",
        "owner-dm-fixture",
        7,
    )
    pending = store.bind_message(
        store.prepare(spec, datetime(2026, 8, 16, tzinfo=UTC)),
        "fixture-message",
    )
    store.archive(pending, store_module.ApprovalState.ARCHIVED, "approved")
    return import_module("todo_execution_claim").ApprovalClaimStore(store.root)


def test_offline_scenario_uses_the_current_worktree_without_runtime_override() -> None:
    # Given: a normal developer shell without a production runtime-root override.
    env = dict(os.environ)
    env.pop("AUTOPHAGY_RUNTIME_ROOT", None)
    env.pop("AUTOPHAGY_REPO_ROOT", None)
    env["AUTOPHAGY_DEMO_SECRET"] = "DUMMY-todo-scenario"

    # When: the documented offline scenario is executed from this worktree.
    completed = subprocess.run(
        ["bash", str(_SCENARIO)],
        cwd=_REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    # Then: runtime-root resolution remains local and the full scenario passes.
    assert completed.returncode == 0, completed.stderr
    assert "APPROVAL-CYCLE-PASS" in completed.stdout
    assert "SCENARIO-PASS" in completed.stdout


# --- contract 2: the writer refuses, writes, then proves the write ------------


class FakeGws:
    """In-memory ``gws`` stand-in that records every argv it is handed."""

    def __init__(self, stored_title: str | None = None, *, empty_get: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.stored_title = stored_title
        self.empty_get = empty_get

    def __call__(self, argv: list[str]) -> dict[str, Any]:
        self.calls.append(tuple(argv))
        body = json.loads(argv[argv.index("--json") + 1]) if "--json" in argv else {}
        if argv[3] == "insert":
            self.stored_title = self.stored_title or str(body["title"])
            return {"id": "task-fixture-1", "title": self.stored_title}
        return {} if self.empty_get else {"id": "task-fixture-1", "title": self.stored_title}

    @property
    def methods(self) -> list[str]:
        return [call[3] for call in self.calls]


def _request(todo: ModuleType, title: str = "합성 과제") -> Any:
    return todo.TaskRequest(tasklist="@default", title=title)


def test_create_task_refused_without_approval_record(todo: ModuleType, tmp_path: Path) -> None:
    """Given no approval record, When creating, Then nothing is executed at all."""
    fake = FakeGws()
    with pytest.raises(todo.ApprovalRequiredError):
        todo.create_task(
            _request(todo),
            runner=fake,
            context=ApprovalContext(tmp_path / "approvals.jsonl", OWNER, False),
        )
    assert fake.calls == []


def test_create_task_inserts_then_verifies_by_reread(todo: ModuleType, tmp_path: Path) -> None:
    """Given an approval, When creating, Then insert is followed by a matching get."""
    log = tmp_path / "approvals.jsonl"
    request = _request(todo)
    claims = _approve(log, todo.evaluate(todo.insert_argv(request), context=ApprovalContext(None, OWNER, False)))

    fake = FakeGws()
    created = todo.create_task(
        request, runner=fake, context=ApprovalContext(log, OWNER, False), claim_store=claims
    )

    assert fake.methods == ["insert", "get"]
    assert created.task_id == "task-fixture-1"
    assert created.title == request.title
    assert created.verified is True


def test_create_task_fails_when_reread_title_mismatches(todo: ModuleType, tmp_path: Path) -> None:
    """A re-read that returns a different title is a hard failure, not a success."""
    log = tmp_path / "approvals.jsonl"
    request = _request(todo)
    claims = _approve(log, todo.evaluate(todo.insert_argv(request), context=ApprovalContext(None, OWNER, False)))

    fake = FakeGws(stored_title="김가상 오기입 제목")
    with pytest.raises(todo.VerificationFailedError):
        todo.create_task(
            request, runner=fake, context=ApprovalContext(log, OWNER, False), claim_store=claims
        )
    assert fake.methods == ["insert", "get"]


def test_create_task_fails_when_reread_returns_no_task(todo: ModuleType, tmp_path: Path) -> None:
    """An empty re-read cannot prove the write — fail closed."""
    log = tmp_path / "approvals.jsonl"
    request = _request(todo)
    claims = _approve(log, todo.evaluate(todo.insert_argv(request), context=ApprovalContext(None, OWNER, False)))

    fake = FakeGws(empty_get=True)
    with pytest.raises(todo.VerificationFailedError):
        todo.create_task(
            request, runner=fake, context=ApprovalContext(log, OWNER, False), claim_store=claims
        )


def test_action_hash_binds_the_exact_title(todo: ModuleType, tmp_path: Path) -> None:
    """One ✅ authorizes one title — a different title must be refused."""
    log = tmp_path / "approvals.jsonl"
    approved = _request(todo, "승인된 제목")
    _approve(log, todo.evaluate(todo.insert_argv(approved), context=ApprovalContext(None, OWNER, False)))

    fake = FakeGws()
    with pytest.raises(todo.ApprovalRequiredError):
        todo.create_task(
            _request(todo, "다른 제목"),
            runner=fake,
            context=ApprovalContext(log, OWNER, False),
        )
    assert fake.calls == []


def test_gws_subprocess_receives_explicit_env(
    todo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """no-agent cron puts no secrets in os.environ — the child env must be explicit."""
    seen: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, '{"id":"t","title":"t"}', "")

    monkeypatch.setattr(todo.subprocess, "run", _fake_run)
    monkeypatch.setenv("TODO_GWS_BIN", "/usr/bin/true")
    todo.run_gws(("gws", "tasks", "tasks", "get", "--params", "{}"))

    assert isinstance(seen["env"], dict)
    assert seen["env"].get("PATH")
    assert seen["capture_output"] is True
    assert seen["text"] is True
    assert seen["check"] is False
    assert isinstance(seen["timeout"], int)
    assert seen["argv"][0] == "/usr/bin/true"

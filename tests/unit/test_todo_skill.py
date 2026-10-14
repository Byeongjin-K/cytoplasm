"""RTS-1 EF4 — Google Tasks writer (`todo` skill) gate + post-write re-read specs.

Two independent contracts are locked here:

1. **Denylist coverage.** Until now a raw ``gws tasks tasks insert`` matched no
   rule in ``configs/external-effect-tools.yaml``, so the production gate
   classified it as a read and let it through — that is how an unapproved write
   reached Google Tasks. The new ``gws_tasks_mutation`` rule must sit ABOVE the
   ``generic_*`` catch-alls (the loader is first-match) and must leave every
   existing rule byte-identical.

2. **Writer behavior.** ``todo_cli.create_task`` must refuse without a valid
   owner approval record, and after a successful insert it must RE-READ the task
   through ``gws tasks tasks get`` and prove the stored title/id are the ones it
   sent. A mismatch or an empty re-read is an explicit failure, never a silent
   success.

The real ``automation.interop.external_effect_gate`` is driven as a library, so
the action hash and approval判定 come from production code unchanged. No network
and no real ``gws`` binary is touched: the CLI's ``runner`` seam takes a fake.
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

from automation.interop import external_effect_gate as gate
from automation.interop.external_effect_gate import ApprovalContext, ToolCall, load_denylist

_REPO = Path(__file__).resolve().parents[2]
_DENYLIST = _REPO / "configs" / "external-effect-tools.yaml"
_SCENARIO = _REPO / "skills" / "todo" / "scripts" / "scenario.sh"
sys.path.insert(0, str(_REPO / "skills" / "todo" / "scripts"))

RULE_ID = "gws_tasks_mutation"
OWNER = "owner-fixture"


@pytest.fixture
def todo() -> ModuleType:
    return import_module("todo_cli")


@pytest.fixture(autouse=True)
def _runtime_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(_REPO))


def _rules() -> tuple[gate.ExternalEffectRule, ...]:
    return load_denylist(_DENYLIST)


def _rule_ids() -> list[str]:
    return [rule.rule_id for rule in _rules()]


def _rule_block(text: str, rule_id: str) -> tuple[str, ...]:
    lines = [line.strip() for line in text.splitlines()]
    start = lines.index("- id: " + rule_id)
    return tuple(lines[start : start + 3])


def _decide(call: ToolCall, log: Path | None = None) -> gate.ExternalEffectDecision:
    return gate.evaluate_tool_call(call, _rules(), ApprovalContext(log, OWNER, False))


def _shell(command: str) -> ToolCall:
    return ToolCall(tool_name="terminal", arguments={"command": command})


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


# --- contract 1: the denylist actually covers Google Tasks writes -------------


def test_denylist_rule_gates_gws_tasks_insert() -> None:
    """Given a raw shell tasks insert, When evaluated, Then it needs approval."""
    decision = _decide(
        _shell('gws tasks tasks insert --params {"tasklist":"@default"} --json {"title":"x"}')
    )
    assert decision.external_effect is True
    assert decision.allowed is False
    assert decision.reason == "approval_required"
    assert decision.target_id.startswith("tool:" + RULE_ID + ":")


def test_denylist_rule_precedes_generic_catch_all() -> None:
    """The loader is first-match, so our rule must win over generic_* rules."""
    ids = _rule_ids()
    assert RULE_ID in ids
    assert ids.index(RULE_ID) < ids.index("generic_external_effect_tool")
    assert ids.index(RULE_ID) < ids.index("generic_external_post_command")


def test_denylist_rule_uses_only_supported_keys() -> None:
    """The parser is a strict line parser: any 4th key fails the whole file closed."""
    block = _rule_block(_DENYLIST.read_text(encoding="utf-8"), RULE_ID)
    assert [line.split(":", 1)[0] for line in block] == ["- id", "tool_name_regex", "arguments_regex"]


def test_tasks_read_commands_stay_ungated() -> None:
    """Reads must not be gated — only mutations are."""
    for command in ("gws tasks tasks list", "gws tasks tasks get", "gws tasks tasklists list"):
        assert _decide(_shell(command)).external_effect is False


def test_existing_denylist_rules_unchanged() -> None:
    """Adding a rule must not disturb the rules that already protect other flows."""
    assert _rule_ids() == [
        "gws_gmail_send",
        "gws_calendar_mutation",
        RULE_ID,
        "mailon_send",
        "obsidian_write_note_push",
        "generic_external_effect_tool",
        "generic_external_post_command",
        "patent_draft_drive_upload",
        "gws_drive_permission_grant",
    ]
    assert _decide(_shell("gws calendar events insert --json {}")).external_effect is True
    assert _decide(_shell("gws gmail +send --to a@b.c")).external_effect is True


def test_scenario_denylist_fixture_matches_repo_rule() -> None:
    """The offline scenario ships its own denylist copy — lock it against drift."""
    assert _rule_block(_SCENARIO.read_text(encoding="utf-8"), RULE_ID) == _rule_block(
        _DENYLIST.read_text(encoding="utf-8"), RULE_ID
    )


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

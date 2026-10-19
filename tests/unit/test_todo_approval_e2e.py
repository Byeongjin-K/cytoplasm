from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_surface import ApprovalKind, ChannelFacts, resolve_new_binding
import unit_node


_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "todo" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_OWNER = "owner-e2e"
_AGENT_CHAT_CHANNEL = "1526487936065952390"
_CHANNEL = "1526487936065952391"
_TITLE = "통합 승인 과제"


@dataclass(slots=True)
class FakeDirectory:
    #: 요청별 스레드 스펙 기록 — e2e는 자기 바인딩을 직접 만들므로 비어 있어야 한다.
    requests: list[tuple[ApprovalKind, object]] = field(default_factory=list)

    def owner_dm(self) -> str:
        raise AssertionError

    def skill_approvals(self) -> str:
        raise AssertionError

    def agent_chat(self) -> str:
        return _AGENT_CHAT_CHANNEL

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        assert kind is ApprovalKind.TODO
        return _CHANNEL

    def agent_chat_request_thread(self, kind: ApprovalKind, request: object) -> str:
        self.requests.append((kind, request))
        return _CHANNEL

    def describe(self, channel_id: str) -> ChannelFacts:
        return ChannelFacts(11, "승인-todo", (), _AGENT_CHAT_CHANNEL)


@dataclass(slots=True)
class FakeDiscord:
    messages: dict[str, str] = field(default_factory=dict)
    reactions: dict[str, dict[str, tuple[tuple[str, bool], ...]]] = field(default_factory=dict)

    def post_message(self, channel_id: str, content: str) -> str:
        message_id = str(1530000000000000000 + len(self.messages) + 1)
        self.messages[message_id] = content
        return message_id

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        del channel_id, message_id, emoji

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        del channel_id
        return self.messages.get(message_id)

    def get_reaction_users(self, channel_id: str, message_id: str, emoji: str):
        del channel_id
        return self.reactions.get(message_id, {}).get(emoji, ())

    def delete_message(self, channel_id: str, message_id: str) -> None:
        del channel_id
        self.messages.pop(message_id, None)


@dataclass(slots=True)
class FakeGws:
    calls: list[str] = field(default_factory=list)

    def __call__(self, argv: list[str]) -> dict[str, Any]:
        self.calls.append(argv[3])
        return {"id": "task-e2e", "title": _TITLE}


def test_command_chain_replay_and_second_generation_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    store_module = import_module("todo_approval_store")
    watch = import_module("todo_confirm_reaction_watch")
    root = tmp_path / "todo-approvals"
    log = tmp_path / "approvals.jsonl"
    store = store_module.TodoApprovalStore(root)
    transport = FakeDiscord()
    directory = FakeDirectory()
    lease = FileKeyLease(root / "approval-leases")
    binding = resolve_new_binding(ApprovalKind.TODO, directory, _OWNER)
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    runtime = approval.ApprovalRuntime(
        store, transport, directory, _OWNER, binding, lease,
        PostingJournal(root / "posting-journal"), lambda: now,
    )
    monkeypatch.setenv("TODO_OWNER_ID", _OWNER)
    monkeypatch.setenv("TODO_APPROVAL_ROOT", str(root))
    monkeypatch.setenv("TODO_APPROVAL_LOG", str(log))
    monkeypatch.setenv("TODO_DENYLIST", str(_REPO / "configs" / "external-effect-tools.yaml"))
    monkeypatch.setattr(
        approval,
        "request_cli_approval",
        lambda intent, owner: approval.request_approval(intent, runtime),
    )
    fake_gws = FakeGws()
    monkeypatch.setattr(todo, "run_gws", fake_gws)

    assert todo.main(["request", "--title", _TITLE]) == 0
    first = store.all_outstanding()[0]
    transport.reactions[first.message_id] = {"✅": ((_OWNER, False),)}
    watch.run_once(
        store=store, owner_id=_OWNER, transport=transport, directory=directory,
        approval_log=log, lease=lease, now=now,
    )
    # The tick that consumed the ✅ also performed the write it authorized, so the manual
    # create path is now a refusal rather than the step that makes the task exist. Repair
    # ticket t_e3243dc5 recorded the old behaviour as occurrences 2 and 4.
    assert fake_gws.calls == ["insert", "get"]
    assert todo.main(["create", "--title", _TITLE]) == 4
    assert fake_gws.calls == ["insert", "get"]

    assert todo.main(["request", "--title", _TITLE]) == 0
    second = store.all_outstanding()[0]
    transport.reactions[second.message_id] = {"✅": ((_OWNER, False),)}
    watch.run_once(
        store=store, owner_id=_OWNER, transport=transport, directory=directory,
        approval_log=log, lease=lease, now=now,
    )
    assert fake_gws.calls == ["insert", "get", "insert", "get"]
    assert todo.main(["create", "--title", _TITLE]) == 4


def test_offline_scenario_reports_full_approval_matrix(tmp_path: Path) -> None:
    env = {
        **unit_node.env(),
        "AUTOPHAGY_DEMO_SECRET": "DUMMY-offline",
        "AUTOPHAGY_RUNTIME_ROOT": str(_REPO),
        "HOME": str(tmp_path),
        "PATH": os.environ["PATH"],
    }
    result = subprocess.run(
        ["bash", str(_SCRIPTS / "scenario.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "APPROVAL-CYCLE-PASS happy=1 failures=4 resume=1 full_cycle=1" in result.stdout

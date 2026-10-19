"""Agent-chat-thread producer contracts for the todo request command."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import Outcome
from automation.interop.approval_surface import (
    ApprovalKind,
    ChannelFacts,
    request_thread_name,
    resolve_new_binding,
)
from automation.interop.effect_approval import ApprovalContext

if TYPE_CHECKING:
    from automation.interop.approval_lifecycle import Verdict
    from todo_approval import ApprovalRuntime, TodoApprovalIntent
    from todo_approval_store import TodoApprovalStore


_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "todo" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_OWNER = "owner-fixture"
_AGENT_CHAT_CHANNEL = "1526487936065952390"
_CHANNEL = "1526487936065952391"
#: 이 요청 하나가 여는 스레드 — 승인 카드·리마인더·결과가 여기서 끝난다.
_REQUEST_THREAD = "1526487936065952392"
_ORIGIN_CHANNEL = "200000000000000001"
_ORIGIN_MESSAGE = "origin-message-1"
_NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


@dataclass(slots=True)
class FakeDirectory:
    described: list[str]
    #: 요청별 스레드 스펙 — 무엇을 제목으로 쓰고 무엇을 앵커로 넘겼는지 기록한다.
    requests: list[tuple[ApprovalKind, object]] = field(default_factory=list)
    #: 만들어진 요청별 스레드 id → 만들 때 쓴 이름(실제 Discord 가 그렇게 기술한다).
    thread_names: dict[str, str] = field(default_factory=dict)
    per_kind: int = 0

    def owner_dm(self) -> str:
        raise AssertionError("todo must not resolve the owner-DM approval surface")

    def skill_approvals(self) -> str:
        raise AssertionError("todo must not resolve the skill approval surface")

    def agent_chat(self) -> str:
        return _AGENT_CHAT_CHANNEL

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        assert kind is ApprovalKind.TODO
        self.per_kind += 1
        return _CHANNEL

    def agent_chat_request_thread(self, kind: ApprovalKind, request: object) -> str:
        self.requests.append((kind, request))
        channel_id = str(int(_REQUEST_THREAD) + len(self.requests) - 1)
        self.thread_names[channel_id] = request_thread_name(kind, request)
        return channel_id

    def describe(self, channel_id: str) -> ChannelFacts:
        self.described.append(channel_id)
        name = self.thread_names.get(channel_id, "승인-todo")
        return ChannelFacts(11, name, (), _AGENT_CHAT_CHANNEL)


@dataclass(slots=True)
class FakeTransport:
    messages: dict[str, str]
    calls: list[tuple[str, ...]]

    def post_message(self, channel_id: str, content: str) -> str:
        message_id = str(1530000000000000000 + len(self.messages) + 1)
        self.messages[message_id] = content
        self.calls.append(("post", channel_id, message_id))
        return message_id

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.calls.append(("react", channel_id, message_id, emoji))

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        self.calls.append(("get", channel_id, message_id))
        return self.messages.get(message_id)

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]:
        self.calls.append(("reactions", channel_id, message_id, emoji))
        return ()

    def delete_message(self, channel_id: str, message_id: str) -> None:
        self.calls.append(("delete", channel_id, message_id))

    def api(self, method: str, path: str, payload: dict | None = None) -> object:
        raise AssertionError("the fake directory owns every channel lookup")


def _intent(
    todo: ModuleType,
    approval: ModuleType,
    title: str = "합성 승인 과제",
    *,
    origin: tuple[str, str] = ("", ""),
) -> TodoApprovalIntent:
    request = todo.TaskRequest("@default", title, notes="본문 비저장", due="2026-08-20T00:00:00Z")
    argv = todo.insert_argv(request)
    decision = todo.evaluate(argv, context=ApprovalContext(None, _OWNER, False))
    return approval.TodoApprovalIntent(
        action_hash=decision.action_hash,
        target_id=decision.target_id,
        argv_summary=approval.masked_argv_summary(argv),
        title=request.title,
        due=request.due,
        origin_channel_id=origin[0],
        origin_message_id=origin[1],
        notes=request.notes,
    )


def _runtime(
    approval: ModuleType,
    store: TodoApprovalStore,
    transport: FakeTransport,
    directory: FakeDirectory,
    now: list[datetime],
    root: Path,
) -> ApprovalRuntime:
    binding = resolve_new_binding(ApprovalKind("todo"), directory, _OWNER)
    return approval.ApprovalRuntime(
        store=store,
        transport=transport,
        directory=directory,
        owner_id=_OWNER,
        binding=binding,
        lease=FileKeyLease(root / "leases"),
        journal=PostingJournal(root / "journal"),
        now=lambda: now[0],
    )


@pytest.fixture(autouse=True)
def _runtime_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(_REPO))


def test_request_posts_agent_chat_thread_card_and_persists_binding(tmp_path: Path) -> None:
    # Given: an isolated store, directory, and fake Discord transport.
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    store_module = import_module("todo_approval_store")
    store = store_module.TodoApprovalStore(tmp_path / "state")
    transport = FakeTransport({}, [])
    directory = FakeDirectory([])
    intent = _intent(todo, approval)

    # When: the producer requests approval through the shared lifecycle façade.
    verdict = approval.request_approval(
        intent,
        _runtime(approval, store, transport, directory, [_NOW], tmp_path),
    )

    # Then: one agent-chat thread card is posted, both reactions are primed, and sensitive notes are absent.
    assert verdict.outcome is Outcome.POSTED
    assert [call[0] for call in transport.calls] == ["post", "react", "react"]
    record = store.active(f"todo:{intent.action_hash}")
    assert record is not None
    assert (record.action_hash, record.target_id) == (intent.action_hash, intent.target_id)
    assert (record.kind, record.surface, record.channel_id) == (
        "todo", "agent-chat-thread", _CHANNEL,
    )
    assert "합성 승인 과제" in next(iter(transport.messages.values()))
    assert "본문 비저장" not in next(iter(transport.messages.values()))


def test_same_hash_reuses_live_pending_and_validates_stored_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one live pending request and a spy on stored-binding validation.
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    surface = import_module("automation.interop.approval_surface")
    store = import_module("todo_approval_store").TodoApprovalStore(tmp_path / "state")
    transport = FakeTransport({}, [])
    directory = FakeDirectory([])
    runtime = _runtime(approval, store, transport, directory, [_NOW], tmp_path)
    intent = _intent(todo, approval)
    first = approval.request_approval(intent, runtime)
    validations: list[str] = []
    real_validate = surface.validate_stored_binding

    def validate(binding, bound_directory, owner_id):
        validations.append(binding.channel_id)
        return real_validate(binding, bound_directory, owner_id)

    monkeypatch.setattr(surface, "validate_stored_binding", validate)

    # When: the exact same request is issued again before TTL.
    second = approval.request_approval(intent, runtime)

    # Then: lifecycle reuses the original message and posts nothing new.
    assert first.outcome is Outcome.POSTED
    assert second.outcome is Outcome.PENDING
    assert len(transport.messages) == 1
    assert validations == [_CHANNEL]


def test_expired_request_archives_then_posts_new_generation_without_delete(tmp_path: Path) -> None:
    # Given: generation one is live and the injected clock advances beyond 24 hours.
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    store_module = import_module("todo_approval_store")
    store = store_module.TodoApprovalStore(tmp_path / "state")
    transport = FakeTransport({}, [])
    directory = FakeDirectory([])
    now = [_NOW]
    runtime = _runtime(approval, store, transport, directory, now, tmp_path)
    intent = _intent(todo, approval)
    _ = approval.request_approval(intent, runtime)
    now[0] += store_module.TODO_APPROVAL_TTL + timedelta(seconds=1)

    # When: the same argv is requested after expiry.
    verdict = approval.request_approval(intent, runtime)

    # Then: a fresh generation is posted while the old Discord message is untouched.
    assert verdict.outcome is Outcome.POSTED
    assert len(transport.messages) == 2
    assert not any(call[0] == "delete" for call in transport.calls)
    assert store.active(f"todo:{intent.action_hash}").generation == 2
    assert [(item.generation, item.state.value) for item in store.archives(f"todo:{intent.action_hash}")] == [(1, "expired")]


def test_plan_does_not_call_legacy_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the adapter's runtime producer is replaced with an in-memory recorder.
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    store = import_module("todo_approval_store").TodoApprovalStore(tmp_path / "todo-approvals")
    transport = FakeTransport({}, [])
    directory = FakeDirectory([])
    runtime = _runtime(approval, store, transport, directory, [_NOW], tmp_path)
    calls: list[str] = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_OWNER_ID", _OWNER)

    def request_cli(intent: TodoApprovalIntent, owner: str) -> Verdict:
        assert owner == _OWNER
        calls.append(intent.action_hash)
        return approval.request_approval(intent, runtime)

    monkeypatch.setattr(approval, "request_cli_approval", request_cli)

    # When: the read-only plan command is invoked through the public parser.
    assert todo.main(["plan", "--title", "합성 요청"]) == 0

    # Then: no legacy approval card or pending generation was produced.
    assert calls == []
    assert transport.messages == {}
    assert store.all_outstanding() == ()


def _cli_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, FakeTransport, FakeDirectory, TodoApprovalStore]:
    """The production ``request_cli_approval`` wired to a fake Discord identity."""
    runtime_module = import_module("todo_approval_runtime")
    root = tmp_path / "todo-approvals"
    transport = FakeTransport({}, [])
    directory = FakeDirectory([])
    monkeypatch.setenv("TODO_APPROVAL_ROOT", str(root))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(
        import_module("todo_discord"), "TodoDiscordTransport", lambda _token: transport
    )
    real_repo_module = runtime_module._repo_module

    def repo_module(name: str) -> object:
        if name == "approval_directory":
            return SimpleNamespace(DiscordChannelDirectory=lambda *_args: directory)
        return real_repo_module(name)

    monkeypatch.setattr(runtime_module, "_repo_module", repo_module)
    store = import_module("todo_approval_store").TodoApprovalStore(root)
    return runtime_module, transport, directory, store


def test_cli_request_opens_its_own_thread_and_persists_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the production producer entry point with a fake Discord identity
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    runtime_module, transport, directory, store = _cli_producer(tmp_path, monkeypatch)
    intent = _intent(todo, approval, origin=(_ORIGIN_CHANNEL, _ORIGIN_MESSAGE))

    # When: the CLI requests owner approval for that task
    verdict = runtime_module.request_cli_approval(intent, _OWNER)

    # Then: the request asked for ITS OWN thread — 제목만(노트 제외), 지시 메시지를 앵커
    # 후보로 넘기고 — and the record remembers that thread for the result notice
    assert verdict.outcome is Outcome.POSTED
    [(kind, spec)] = directory.requests
    assert kind is ApprovalKind.TODO
    assert spec.title == "합성 승인 과제"
    assert "본문 비저장" not in spec.title
    assert (spec.origin_channel_id, spec.origin_message_id) == (_ORIGIN_CHANNEL, _ORIGIN_MESSAGE)
    assert directory.per_kind == 0
    record = store.active(intent.key)
    assert record is not None
    assert record.channel_id == _REQUEST_THREAD
    assert record.approval_thread_id == _REQUEST_THREAD
    assert record.action_hash == intent.action_hash
    assert [call[1] for call in transport.calls if call[0] == "post"] == [_REQUEST_THREAD]


def test_repeated_cli_requests_reuse_the_first_request_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one request of this argv already posted into its own thread
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    runtime_module, transport, directory, store = _cli_producer(tmp_path, monkeypatch)
    intent = _intent(todo, approval, origin=(_ORIGIN_CHANNEL, _ORIGIN_MESSAGE))
    first = runtime_module.request_cli_approval(intent, _OWNER)

    # When: the same argv is requested twice more before the owner decides
    second = runtime_module.request_cli_approval(intent, _OWNER)
    third = runtime_module.request_cli_approval(intent, _OWNER)

    # Then: one approval key keeps ONE thread — no empty orphan per re-request
    assert (first.outcome, second.outcome, third.outcome) == (
        Outcome.POSTED, Outcome.PENDING, Outcome.PENDING,
    )
    assert len(directory.requests) == 1
    assert directory.per_kind == 0

    # …and every post of that key landed in that one thread
    assert [call[1] for call in transport.calls if call[0] == "post"] == [_REQUEST_THREAD]
    record = store.active(intent.key)
    assert record is not None
    assert (record.channel_id, record.approval_thread_id) == (_REQUEST_THREAD, _REQUEST_THREAD)


def test_legacy_todo_adapter_remains_registered_for_stored_approvals() -> None:
    # Given / When: the shared inventory is read for the retained legacy adapter.
    inventory = import_module("approval_conformance_inventory")
    adapter = "skills/todo/scripts/todo_approval.py"

    # Then: saved approvals still use the registered lifecycle and record store.
    assert inventory._LIFECYCLE_HOSTS[adapter] == adapter
    assert "skills/todo/scripts/todo_approval.py::TodoApprovalGate.post" in inventory._ADAPTER_POSTERS
    assert inventory._RECORD_WRITERS[
        "skills/todo/scripts/todo_approval.py::TodoApprovalGate.commit"
    ] == "skills/todo/scripts/todo_approval_store.py"

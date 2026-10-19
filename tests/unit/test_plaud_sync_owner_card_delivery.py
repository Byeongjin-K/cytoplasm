"""Real local persistence and lifecycle probes for OMUX 23 cards."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from automation.interop import owner_message as om
from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import Outcome, Probe
from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ApprovalSurface
from automation.plaud_sync import approval_gate as pg, store as ps
from tests.unit.test_plaud_sync_owner_cards import PLAUD_RECORD, PREVIEW
from tests.unit.test_plaud_sync_approval_gate import FakeTransport as PlaudTransport


@pytest.fixture(autouse=True)
def synthetic_todo_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reuse the existing wire fake with small, visibly synthetic coordinates."""
    from tests.unit import test_todo_approval_producer as fixtures
    monkeypatch.setattr(fixtures, "_AGENT_CHAT_CHANNEL", "111")
    monkeypatch.setattr(fixtures, "_CHANNEL", "222")
    monkeypatch.setattr(fixtures, "_REQUEST_THREAD", "333")
    def post_message(transport: fixtures.FakeTransport, channel_id: str, content: str) -> str:
        transport.messages["444"] = content
        transport.calls.append(("post", channel_id, "444"))
        return "444"
    monkeypatch.setattr(fixtures.FakeTransport, "post_message", post_message)


@pytest.mark.parametrize("producer", ["plaud"])
@pytest.mark.parametrize("fallback", [False, True])
def test_selected_version_persists_when_posted_and_the_real_probe_accepts(
    producer: str, fallback: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given real file stores, the real lifecycle and a wire-level Discord fake.
    if fallback:
        def refuse(message: om.OwnerMessage, *, destination: om.Ref) -> str:
            raise om.OwnerMessageError(detail="test")
        monkeypatch.setattr(om, "render", refuse)
    binding = ApprovalBinding(ApprovalKind.OBSIDIAN_WRITE, ApprovalSurface.AGENT_CHAT_THREAD, "222", 8)
    lease, journal = FileKeyLease(tmp_path / "leases"), PostingJournal(tmp_path / "journal")
    if producer == "plaud":
        record = PLAUD_RECORD
        ps.save_state(tmp_path / "state.json", ps.PlaudSyncState(1, None, {record.recording_id: record}))
        store = ps.PlaudSyncStore(tmp_path / "state.json")
        transport = PlaudTransport()
        # When the new card is posted.
        verdict = pg.request_approval(record, preview=PREVIEW, store=store, transport=transport,
                                      binding=binding, lease=lease, journal=journal)
        persisted = store.pending()[0]
        transport.content = transport.posted[0][1]
        gate = pg.PlaudApprovalGate(persisted, store, transport)
        expected_version = "plaud-sync-render-v4" if fallback else "plaud-sync-render-v7"
        key = record.recording_id
    # Then persisted presentation is separate from binding; probe itself accepts the card.
    assert verdict.outcome is Outcome.POSTED
    assert persisted.render_version == expected_version
    assert persisted.action_hash == record.action_hash
    assert persisted.version == 1
    assert gate.probe(gate.outstanding(key)[0]) is Probe.BOUND_PENDING


@pytest.mark.parametrize("producer", ["plaud", "todo"])
def test_oversized_card_is_refused_before_request_thread_creation(
    producer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an impossible card and the real production effect assembly.
    if producer == "plaud":
        from tests.unit.test_plaud_sync_effects import _notifier, _BODY
        record = replace(PLAUD_RECORD, note_title="제" * 1901)
        ps.save_note_body(tmp_path, record.recording_id, _BODY)
        import hashlib
        record = replace(record, body_sha256=hashlib.sha256(_BODY.encode()).hexdigest())
        ps.save_state(tmp_path / "plaud.json", ps.PlaudSyncState(1, None, {record.recording_id: record}))
        effects, _ = _notifier(tmp_path, monkeypatch)
        calls = []
        def resolve(*args, **kwargs):
            calls.append("thread")
            return ApprovalBinding(ApprovalKind.OBSIDIAN_WRITE, ApprovalSurface.AGENT_CHAT_THREAD, "222", 8)
        monkeypatch.setattr("automation.plaud_sync.effects_live.resolve_new_binding", resolve)
        # When rendered through the live effect surface.
        result = effects.post_approval(record)
    else:
        from tests.unit.test_todo_approval_producer import _cli_producer, _OWNER
        runtime, transport, directory, _ = _cli_producer(tmp_path, monkeypatch)
        if TYPE_CHECKING:
            from skills.todo.scripts.todo_approval import TodoApprovalIntent
            from skills.todo.scripts.todo_approval_render import TodoRenderError
        else:
            from todo_approval import TodoApprovalIntent
            from todo_approval_render import TodoRenderError
        intent = TodoApprovalIntent("sha256:abc", "target", "masked", "제" * 1901, None)
        # When rendered through the CLI producer.
        with pytest.raises(TodoRenderError):
            runtime.request_cli_approval(intent, _OWNER)
        result, calls = None, directory.requests
        assert transport.calls == []
    # Then failure preceded any request thread creation.
    assert result is None
    assert calls == []


@pytest.mark.parametrize("producer", ["plaud", "todo"])
@pytest.mark.parametrize("intact", [True, False])
def test_producer_probe_checks_the_wire_when_a_published_card_is_read(
    producer: str, intact: bool, tmp_path: Path,
) -> None:
    # Given independently pinned card bytes on a published message, with an unknown
    # stored render version: the live probe must never need to replay the renderer.
    from tests.unit.test_plaud_sync_owner_card_versions import PLAUD_V5, TODO_V2
    if producer == "plaud":
        record = replace(PLAUD_RECORD, message_id="333", channel_id="222", render_version="unknown")
        ps.save_state(tmp_path / "state.json", ps.PlaudSyncState(1, None, {"rec-001": record}))
        store = ps.PlaudSyncStore(tmp_path / "state.json")
        content = PLAUD_V5 if intact else PLAUD_V5.replace(record.action_hash, "sha256:wrong")
        transport = PlaudTransport(content=content)
        gate = pg.PlaudApprovalGate(record, store, transport)
        request = gate.outstanding("rec-001")[0]
    else:
        from tests.unit.test_todo_approval_producer import _runtime, FakeDirectory, FakeTransport
        if TYPE_CHECKING:
            from skills.todo.scripts.todo_approval import TodoApprovalGate, TodoApprovalIntent
            from skills.todo.scripts.todo_approval_store import TodoApprovalStore
        else:
            from todo_approval import TodoApprovalGate, TodoApprovalIntent
            from todo_approval_store import TodoApprovalStore
        from automation.interop.approval_lifecycle import ApprovalRequest
        store = TodoApprovalStore(tmp_path / "state")
        transport = FakeTransport({"333": TODO_V2 if intact else TODO_V2.replace("sha256:abc", "sha256:wrong")}, [])
        runtime = _runtime(import_module("todo_approval"), store, transport, FakeDirectory([]),
                           [datetime(2026, 9, 11, 12, tzinfo=UTC)], tmp_path)
        gate = TodoApprovalGate(TodoApprovalIntent(
            "sha256:abc", "target", "masked", "합성 과제", "2026-09-12"), runtime)
        record = store.prepare(gate._spec(), datetime(2026, 9, 11, 12, tzinfo=UTC))
        store.bind_message(record, "333", render_version="unknown")
        request = ApprovalRequest(record.key, record.action_hash, "333", record.channel_id, "2026-09-11T12:00:00Z")
    # When the producer's own probe reads the wire.
    decision = gate.probe(request)
    # Then only an intact hash binds, regardless of the renderer's availability/version.
    assert decision is (Probe.BOUND_PENDING if intact else Probe.BINDING_MISMATCH)


@pytest.mark.parametrize("fallback", [False, True])
def test_todo_selected_card_is_bound_when_the_real_cli_requests_it(
    fallback: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the real CLI producer and local store, with Discord replaced only at transport.
    from tests.unit.test_todo_approval_producer import _cli_producer, _runtime, _OWNER, _REPO
    if TYPE_CHECKING:
        from skills.todo.scripts.todo_approval import TodoApprovalGate, TodoApprovalIntent
        from skills.todo.scripts.todo_approval_store import approval_ttl
    else:
        from todo_approval import TodoApprovalGate, TodoApprovalIntent
        from todo_approval_store import approval_ttl
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(_REPO))
    monkeypatch.setenv("TODO_OWNER_ID", _OWNER)
    runtime_module, transport, directory, store = _cli_producer(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_module, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 9, 11, 12, tzinfo=tz)))
    if fallback:
        def refuse(message: om.OwnerMessage, *, destination: om.Ref) -> str:
            raise om.OwnerMessageError(detail="synthetic")
        monkeypatch.setattr(om, "render", refuse)
    # When the actual CLI entry point creates one approval.
    result = import_module("todo_cli").main(["request", "--title", "합성 과제"])
    # Then the stored version names the posted bytes and the producer's probe binds them.
    assert result == 0
    record, = store.all_outstanding()
    assert record.render_version == ("todo-render-v1" if fallback else "todo-render-v3")
    binding = ApprovalBinding(ApprovalKind.TODO, ApprovalSurface.AGENT_CHAT_THREAD, record.channel_id, record.policy_version)
    runtime = replace(_runtime(import_module("todo_approval"), store, transport, directory,
                               [record.created_at + approval_ttl() / 2], tmp_path), binding=binding)
    gate = TodoApprovalGate(TodoApprovalIntent(record.action_hash, record.target_id, record.argv_summary, record.title, record.due), runtime)
    assert gate.probe(gate.outstanding(record.key)[0]) is Probe.BOUND_PENDING


@pytest.mark.parametrize("producer", ["plaud"])
@pytest.mark.parametrize("surface", ["request", "effects"])
def test_bound_request_reuses_without_renderer_calls(
    producer: str, surface: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = import_module("tests.unit.test_plaud_sync_owner_card_versions")
    from automation.obsidian_write.config import ObsidianWriteConfig
    from automation.interop.approval_surface import ChannelFacts
    import hashlib
    body = import_module("tests.unit.test_plaud_sync_effects")._BODY
    lease, journal = FileKeyLease(tmp_path / "leases"), PostingJournal(tmp_path / "journal")

    binding = ApprovalBinding(ApprovalKind.OBSIDIAN_WRITE, ApprovalSurface.AGENT_CHAT_THREAD, "222", 8)
    if producer == "plaud":
        record = replace(PLAUD_RECORD, message_id="333", channel_id="222",
                         approval_thread_id="222", body_sha256=hashlib.sha256(body.encode()).hexdigest())
        path = tmp_path / "state.json"
        ps.save_state(path, ps.PlaudSyncState(1, None, {record.recording_id: record}))
        ps.save_note_body(tmp_path, record.recording_id, body)
        store = plaud_store = ps.PlaudSyncStore(path)
        transport = plaud_transport = PlaudTransport(content=versions.PLAUD_V5)
        def request():
            return pg.request_approval(plaud_store.pending()[0], preview=PREVIEW, store=plaud_store,
                                       transport=plaud_transport, binding=binding, lease=lease, journal=journal)
        def unknown_version():
            plaud_store.update(replace(plaud_store.pending()[0], render_version="unknown"))
        def wire():
            return plaud_transport.content
        posts, deleted = transport.posted, transport.deleted
        module = import_module("automation.plaud_sync.effects_live")
    if surface == "effects":
        monkeypatch.setattr(transport, "api", lambda *args: None, raising=False)
        directory = SimpleNamespace(describe=lambda channel: ChannelFacts(11, "synthetic request", (), "111"),
                                    agent_chat=lambda: "111")
        monkeypatch.setattr(module, "DiscordTransport", lambda *args: transport)
        monkeypatch.setattr(module, "DiscordChannelDirectory", lambda *args: directory)
        monkeypatch.setattr(module, "load_config", lambda: ObsidianWriteConfig("git@example.invalid:owner/vault.git", tmp_path / "clone"))
        extra = {}
        effects = module.build_effects(state_path=path, token="synthetic", owner_id=transport.owner_id,
                                       now=datetime(2026, 9, 11, 12, tzinfo=UTC), **extra)
        def request():
            return effects.post_approval(store.pending()[0])
    before = wire()
    calls = []
    real = om.render
    def observe(message, *, destination):
        calls.append(message)
        return real(message, destination=destination)
    monkeypatch.setattr(om, "render", observe)
    result = request()
    assert calls == []
    assert result == ("333", "222") if surface == "effects" else result.outcome is Outcome.PENDING
    assert wire() == before
    assert posts == [] and deleted == []
    assert store.pending()[0].message_id == "333"
    unknown_version()
    result = request()
    assert result == ("333", "222") if surface == "effects" else result.outcome is Outcome.PENDING
    assert calls == [] and wire() == before and posts == [] and deleted == []
    assert store.pending()[0].render_version == "unknown"
    print(f"{producer}/{surface}: unknown_version_accepted=True;  renderer_calls={len(calls)} stored_card_untouched={wire() == before} posts={len(posts)} deletes={len(deleted)}")


@pytest.mark.parametrize("surface", ["cli", "request"])
def test_todo_bound_request_reuses_without_renderer_calls(
    surface: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = import_module("tests.unit.test_todo_approval_producer")
    _cli_producer, _runtime, _OWNER = fixtures._cli_producer, fixtures._runtime, fixtures._OWNER
    approval = import_module("todo_approval")
    TodoApprovalIntent, request_approval = approval.TodoApprovalIntent, approval.request_approval
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(Path.cwd()))
    module, transport, directory, store = _cli_producer(tmp_path, monkeypatch)
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda tz: now))
    intent = TodoApprovalIntent("sha256:abc", "target", "masked", "synthetic", None)
    if surface == "cli":
        def request():
            return module.request_cli_approval(intent, _OWNER)
    else:
        runtime = _runtime(import_module("todo_approval"), store, transport, directory, [now], tmp_path)
        def request():
            return request_approval(intent, runtime)
    assert request().outcome is Outcome.POSTED
    before, record = dict(transport.messages), store.active(intent.key)
    threads = len(directory.requests)
    calls = []
    real = om.render
    def observe(message, *, destination):
        calls.append(message)
        return real(message, destination=destination)
    monkeypatch.setattr(om, "render", observe)
    assert request().outcome is Outcome.PENDING
    assert calls == []
    assert transport.messages == before and store.active(intent.key) == record
    assert len(directory.requests) == threads
    assert not any(call[0] == "delete" for call in transport.calls)
    print(f"todo/{surface}: renderer_calls={len(calls)} stored_card_untouched={transport.messages == before} new_threads={len(directory.requests) - threads}")


def test_todo_expiry_budget_refusal_has_zero_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures = import_module("tests.unit.test_todo_approval_producer")
    _cli_producer, _OWNER = fixtures._cli_producer, fixtures._OWNER
    approval = import_module("todo_approval")
    TodoApprovalIntent, TodoApprovalError = approval.TodoApprovalIntent, approval.TodoApprovalError
    renderer = import_module("todo_approval_render")
    prepare_approval_card, render_todo_approval, TodoRenderError = renderer.prepare_approval_card, renderer.render_todo_approval, renderer.TodoRenderError
    from automation.interop.approval_lifecycle import ApprovalSurfaceError
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(Path.cwd()))
    monkeypatch.setenv("TODO_APPROVAL_TTL", "3600")
    module, transport, directory, store = _cli_producer(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 9, 11, 12, tzinfo=UTC)))
    intent = TodoApprovalIntent("sha256:abc", "target", "masked", "x" * 1714, None)
    version, shorter = prepare_approval_card(intent)
    assert len(shorter) <= 1900
    with pytest.raises(TodoRenderError):
        render_todo_approval(intent, datetime(2026, 9, 11, 13, tzinfo=UTC), render_version=version)
    refusal = None
    try:
        module.request_cli_approval(intent, _OWNER)
    except (TodoRenderError, TodoApprovalError, ApprovalSurfaceError) as error:
        refusal = type(error).__name__
    observed = (len(directory.requests), store.active(intent.key),
                list((store.root / "posting-journal").glob("**/*.json")))
    assert observed == (0, None, [])
    assert transport.messages == {}
    assert refusal is not None
    print(f"expiry refusal={refusal} threads={observed[0]} pending={observed[1]} journal={observed[2]} posts={len(transport.messages)}")


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_todo_preflight_card_survives_thread_creation(
    fallback: bool, existing: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta
    fixtures = import_module("tests.unit.test_todo_approval_producer")
    _cli_producer, FakeDirectory, _OWNER = fixtures._cli_producer, fixtures.FakeDirectory, fixtures._OWNER
    approval = import_module("todo_approval")
    TodoApprovalIntent, TodoApprovalGate, ApprovalRuntime = approval.TodoApprovalIntent, approval.TodoApprovalGate, approval.ApprovalRuntime
    approval_ttl = import_module("todo_approval_store").approval_ttl
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(Path.cwd()))
    monkeypatch.setenv("TODO_APPROVAL_TTL", "3600")
    module, transport, directory, store = _cli_producer(tmp_path, monkeypatch)
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    clock = [now]
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda tz: clock[0]))
    intent = TodoApprovalIntent("sha256:abc", "target", "masked", "synthetic", None)
    anchor = now - timedelta(minutes=10) if existing else now
    if existing:
        binding = ApprovalBinding(ApprovalKind.TODO, ApprovalSurface.AGENT_CHAT_THREAD, "333", 8)
        runtime = ApprovalRuntime(store, transport, directory, _OWNER, binding,
                                  FileKeyLease(tmp_path / "lease"), PostingJournal(tmp_path / "journal"), lambda: anchor)
        store.prepare(TodoApprovalGate(intent, runtime)._spec(), anchor)
    if fallback:
        monkeypatch.setattr(om, "render", None)
    real_open = FakeDirectory.agent_chat_request_thread
    prepared = []
    real_prepare = module.prepare_request_card
    def prepare(*args):
        card = real_prepare(*args)
        prepared.append(card)
        return card
    monkeypatch.setattr(module, "prepare_request_card", prepare)
    def open_thread(self, kind, request):
        assert len(prepared) == 1
        clock[0] += timedelta(minutes=1)
        def unavailable(*args, **kwargs):
            raise AssertionError("renderer called after thread creation")
        monkeypatch.setattr(om, "render", unavailable)
        return real_open(self, kind, request)
    monkeypatch.setattr(FakeDirectory, "agent_chat_request_thread", open_thread)
    assert module.request_cli_approval(intent, _OWNER).outcome is Outcome.POSTED
    record = store.active(intent.key)
    assert record.created_at == anchor
    assert record.render_version == ("todo-render-v1" if fallback else "todo-render-v3")
    assert transport.messages[record.message_id] == prepared[0][1]
    if not fallback:
        assert (anchor + approval_ttl()).strftime("%Y-%m-%d %H:%M") in prepared[0][1]


def test_todo_deadline_and_version_when_real_generation_is_prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a fixed clock and real pending store, with the renderer observed at its boundary.
    from tests.unit.test_todo_approval_producer import _runtime, FakeDirectory, FakeTransport
    if TYPE_CHECKING:
        from skills.todo.scripts.todo_approval import request_approval, TodoApprovalIntent
        from skills.todo.scripts.todo_approval_store import TodoApprovalStore
    else:
        from todo_approval import request_approval, TodoApprovalIntent
        from todo_approval_store import TodoApprovalStore
    observed = []
    real = om.render
    def observe(message: om.OwnerMessage, *, destination: om.Ref) -> str:
        observed.append(message.detail)
        return real(message, destination=destination)
    monkeypatch.setattr(om, "render", observe)
    monkeypatch.setenv("TODO_APPROVAL_TTL", "3600")
    store = TodoApprovalStore(tmp_path / "state")
    runtime = _runtime(import_module("todo_approval"), store, FakeTransport({}, []), FakeDirectory([]),
                       [datetime(2026, 9, 11, 12, tzinfo=UTC)], tmp_path)
    intent = TodoApprovalIntent("sha256:abc", "target", "masked", "합성 과제", "2026-09-12")
    # When requested through the real shared lifecycle.
    verdict = request_approval(intent, runtime)
    # Then the capability check invents no timestamp, and the actual card uses the stored anchor.
    assert verdict.outcome is Outcome.POSTED
    assert observed == [om.Approval(None, "등록 취소"), om.Approval(datetime(2026, 9, 11, 13, tzinfo=UTC), "등록 취소")]
    record = store.active(intent.key)
    assert record is not None
    assert record.render_version == "todo-render-v3"

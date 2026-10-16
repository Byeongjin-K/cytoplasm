from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.interop.approval_lifecycle import ApprovalRequest, Outcome, PostedApproval, Verdict
from automation.interop.approval_surface import (
    ApprovalKind,
    ApprovalSurfaceError,
    KIND_LABELS,
    ChannelFacts,
    RequestThread,
    request_thread_name,
)
from automation.memory_curator.binding import entry_digest
from automation.interop import reaction_approval
from automation.memory_relocate.effects_live import (
    DiscordTransport,
    RelocationStore,
    RelocationStoreError,
    build_effects,
    record_push_approval,
    recover_entry_text,
)
from automation.memory_relocate.model import RelocationRecord, RelocationState, record_key
from automation.memory_relocate.store import load_state, save_state
from automation.memory_relocate.watch_step import ResolveEffects
from automation.obsidian_write.config import ObsidianWriteConfig


def _record(*, entry_text: str, message_id: str | None = None) -> RelocationRecord:
    return RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256=entry_digest("memory", entry_text),
        note_relpath="000_PARA/Resource/relocated.md",
        note_plan_sha256="b" * 64,
        reclaimable_chars=len(entry_text),
        action_hash=f"sha256:{'c' * 64}",
        status="proposed",
        kind="obsidian-write",
        surface="owner-dm",
        channel_id="123456789",
        policy_version=6,
        message_id=message_id,
        created_at="2026-07-31T14:00:00Z",
        approved_at=None,
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=None,
        rag_source_key=None,
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
    )


def _save(path: Path, record: RelocationRecord) -> None:
    save_state(
        path,
        RelocationState(
            version=1,
            relocations={record_key(record.source_kind, record.entry_sha256): record},
        ),
    )


def test_effects_live_reuses_the_shared_reaction_transport() -> None:
    """The transport is imported, never re-implemented (it was copied twice before).

    The reaction transport and the ✅→gate-record transcription now live in
    ``automation.interop.reaction_approval``; this module only re-exports them, so the
    cron wrapper and the approval gate keep their import path and a fix lands once.
    """
    assert DiscordTransport is reaction_approval.DiscordTransport
    assert record_push_approval is reaction_approval.record_push_approval


def test_relocation_store_when_message_is_already_bound_refuses_overwrite(tmp_path: Path) -> None:
    # Given: an unbound persisted relocation and its concrete store.
    entry_text = "owner-approved relocation target"
    record = _record(entry_text=entry_text)
    path = tmp_path / "relocations.json"
    _save(path, record)
    store = RelocationStore(path)

    # When: two different message bindings are requested.
    store.set_message_id(record, "message-first", "channel-first")

    # Then: only the first immutable binding survives.
    with pytest.raises(RelocationStoreError):
        store.set_message_id(record, "message-second", "channel-second")
    persisted = load_state(path).relocations[record_key(record.source_kind, record.entry_sha256)]
    assert persisted.message_id == "message-first"


def test_relocation_store_when_exact_binding_is_recommitted_is_idempotent(
    tmp_path: Path,
) -> None:
    # Given: a relocation whose exact message and channel binding already committed.
    entry_text = "owner-approved relocation target"
    record = _record(entry_text=entry_text)
    path = tmp_path / "relocations.json"
    _save(path, record)
    store = RelocationStore(path)
    store.set_message_id(record, "message-first", "channel-first")

    # When: crash recovery recommits the identical four-field binding.
    store.set_message_id(record, "message-first", "channel-first")

    # Then: it succeeds as a no-op and keeps the original record binding.
    persisted = load_state(path).relocations[record_key(record.source_kind, record.entry_sha256)]
    assert persisted.message_id == "message-first"
    assert persisted.channel_id == "channel-first"


def test_relocation_store_when_record_is_updated_roundtrips_replacement(tmp_path: Path) -> None:
    # Given: a bound record awaiting its approved-state timestamp.
    entry_text = "relocation target that remains immutable"
    record = _record(entry_text=entry_text, message_id="message-bound")
    path = tmp_path / "relocations.json"
    _save(path, record)
    store = RelocationStore(path)
    replacement = replace(
        record,
        status="approved",
        approved_at="2026-07-31T14:30:00Z",
    )

    # When: the concrete store persists the replacement.
    store.update(replacement)

    # Then: reloading produces exactly that typed record.
    persisted = load_state(path).relocations[record_key(record.source_kind, record.entry_sha256)]
    assert persisted == replacement


def test_recover_entry_text_when_native_entry_matches_digest_returns_exact_text(tmp_path: Path) -> None:
    # Given: a native memory file with the bound entry and an unrelated neighbor.
    entry_text = "first line\nsecond line"
    other_text = "native entry that must remain"
    _ = (tmp_path / "MEMORY.md").write_text(
        f"{entry_text}\n§\n{other_text}",
        encoding="utf-8",
    )
    record = _record(entry_text=entry_text)

    # When: live recovery searches source-qualified entry digests.
    recovered = recover_entry_text(tmp_path, record)

    # Then: it returns the parser's exact entry text.
    assert recovered == entry_text


def test_recover_entry_text_when_native_digest_is_absent_returns_none(tmp_path: Path) -> None:
    # Given: native memory no longer contains the record's original text.
    _ = (tmp_path / "MEMORY.md").write_text("replacement native entry", encoding="utf-8")
    record = _record(entry_text="removed or edited native entry")

    # When: live recovery searches the current native entries.
    recovered = recover_entry_text(tmp_path, record)

    # Then: no stale content is reconstructed for an external write or delete.
    assert recovered is None



def test_relocation_store_when_binding_a_message_persists_its_channel(tmp_path: Path) -> None:
    # Given: a proposed record whose approval surface is not resolved yet (empty channel).
    path = tmp_path / "relocations.json"
    entry_text = "gws example@example.com: calendar, drive, sheets, tasks reference."
    record = replace(_record(entry_text=entry_text), channel_id="")
    _save(path, record)
    store = RelocationStore(path)

    # When: the gate commits the posted approval together with the channel it was posted to.
    store.set_message_id(record, "message-1", "channel-9")

    # Then: BOTH are persisted — the next tick must probe the real surface, never an empty channel
    # (a lost channel binding silently discards the owner's ✅).
    stored = load_state(path).relocations[record_key(record.source_kind, record.entry_sha256)]
    assert stored.message_id == "message-1"
    assert stored.channel_id == "channel-9"


def test_push_approval_record_lets_the_external_effect_gate_accept_the_owner_write(
    tmp_path: Path,
) -> None:
    # Given: the note plan cha approved (the composite hash binds exactly relpath+title+body),
    # and an external-effect gate that has no approval record for the Obsidian push yet.
    from automation.interop.external_effect_gate import ApprovalContext
    from automation.memory_relocate.effects_live import record_push_approval
    from automation.memory_relocate.plan import build_relocation_plan
    from automation.obsidian_write import gate_binding

    plan = build_relocation_plan("gws example: calendar, drive, sheets, tasks reference entry.")
    approval_log = tmp_path / "approvals.jsonl"
    context = ApprovalContext(approval_log=approval_log, owner_id="owner-1", e2e_test_mode=False)
    before = gate_binding.evaluate(plan.note_plan, context=context)
    assert before.allowed is False  # the push is denylisted until the owner approved it

    # When: the owner's ✅ on the relocation approval message is transcribed into the gate's log.
    record_push_approval(
        approval_log,
        action_hash=before.action_hash,
        target_id=before.target_id,
        owner_id="owner-1",
        message_id="message-1",
    )

    # Then: the production gate accepts THIS exact push (and nothing else).
    after = gate_binding.evaluate(plan.note_plan, context=context)
    assert after.allowed is True


@dataclass(frozen=True, slots=True)
class _StubBinding:
    """The routing metadata ``post`` reads off a resolved approval binding."""

    channel_id: str
    guild_id: str | None = None


def _effects_with_verdict(
    verdict: Verdict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    specs: list[object] | None = None,
) -> ResolveEffects:
    """Assemble the live effects with every config/network boundary replaced by a stub."""
    module = "automation.memory_relocate.effects_live"

    def fake_load_config() -> ObsidianWriteConfig:
        return ObsidianWriteConfig("git@example.invalid:owner/vault.git", tmp_path / "clone")

    def fake_recover_entry_text(memory_dir: Path, record: RelocationRecord) -> str | None:
        del memory_dir, record
        return "entry"

    def fake_resolve_new_binding(
        kind: object,
        directory: object,
        owner_id: str,
        *,
        request: object = None,
    ) -> _StubBinding:
        del kind, directory, owner_id
        if specs is not None:
            specs.append(request)
        return _StubBinding("chan-9")

    def fake_request_approval(
        record: RelocationRecord,
        entry_text: str,
        *,
        store: object,
        transport: object,
        binding: object,
        lease: object,
        journal: object,
        card: tuple[str, str],
    ) -> Verdict:
        del record, entry_text, store, transport, binding, lease, journal, card
        return verdict

    monkeypatch.setattr(f"{module}.load_config", fake_load_config)
    monkeypatch.setattr(f"{module}.recover_entry_text", fake_recover_entry_text)
    monkeypatch.setattr(f"{module}.resolve_new_binding", fake_resolve_new_binding)
    monkeypatch.setattr(f"{module}.request_approval", fake_request_approval)
    return build_effects(
        memory_dir=tmp_path / "memory",
        state_path=tmp_path / "relocations.json",
        rag_state_path=tmp_path / "rag-state.json",
        token="t",
        owner_id="9",
        now=datetime(2026, 7, 31, 14, 30, tzinfo=UTC),
    )


def test_post_effect_opens_a_request_thread_titled_by_the_note_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a persisted relocation whose filename identifies the note under review.
    entry_text = "운영 참고: 반출 절차 본문 그대로"
    record = _record(entry_text=entry_text)
    _save(tmp_path / "relocations.json", record)
    specs: list[object] = []
    effects = _effects_with_verdict(
        Verdict(Outcome.POSTED, posted=PostedApproval("msg-1", "chan-9")),
        monkeypatch,
        tmp_path,
        specs,
    )

    # When: the tick posts the approval request.
    receipt = effects.post_approval(record)

    # Then: only the note filename is passed, not the pending id or vault directories.
    assert receipt == ("msg-1", "chan-9")
    assert [getattr(spec, "title", None) for spec in specs] == ["relocated.md"]

    # And: the Discord name uses the shipped label without exposing the entry or path.
    name = request_thread_name(ApprovalKind.OBSIDIAN_WRITE, specs[0])
    assert name == f"📝 {KIND_LABELS[ApprovalKind.OBSIDIAN_WRITE]} · relocated.md"
    assert len(name) <= 100
    for secret in (entry_text, record.note_relpath):
        assert secret not in name


def test_post_effect_persists_the_approval_thread_id_beside_the_unchanged_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a persisted relocation awaiting its approval post.
    record = _record(entry_text="operational reference worth relocating")
    path = tmp_path / "relocations.json"
    _save(path, record)
    effects = _effects_with_verdict(
        Verdict(Outcome.POSTED, posted=PostedApproval("msg-1", "chan-9")),
        monkeypatch,
        tmp_path,
    )

    # When: the tick posts the approval request.
    _ = effects.post_approval(record)

    # Then: the record names the approval thread, and the action hash it binds is untouched.
    persisted = load_state(path).relocations[record_key(record.source_kind, record.entry_sha256)]
    assert persisted.approval_thread_id == "chan-9"
    assert persisted.action_hash == record.action_hash
    assert persisted.note_plan_sha256 == record.note_plan_sha256


def test_post_effect_returns_the_message_and_channel_it_posted_to(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a lifecycle verdict that posted the approval to one concrete approval channel.
    record = _record(entry_text="operational reference worth relocating")
    _save(tmp_path / "relocations.json", record)
    posted = _effects_with_verdict(
        Verdict(Outcome.POSTED, posted=PostedApproval("msg-1", "chan-9")),
        monkeypatch,
        tmp_path,
    )

    # When: the tick asks the live effect for its posting receipt.
    receipt = posted.post_approval(record)

    # Then: BOTH halves of the binding come back — the tick cannot re-derive the channel.
    assert receipt == ("msg-1", "chan-9")

    # And: nothing posted means no binding at all, never a half one.
    unposted = _effects_with_verdict(Verdict(Outcome.PENDING), monkeypatch, tmp_path)
    assert unposted.post_approval(record) is None


def test_post_effect_returns_an_adopted_live_binding_as_a_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: enriched-journal recovery adopted a live message instead of posting a new one.
    record = _record(entry_text="operational reference worth relocating")
    _save(tmp_path / "relocations.json", record)
    request = ApprovalRequest(
        record_key(record.source_kind, record.entry_sha256),
        record.action_hash,
        "adopted-message",
        "adopted-channel",
        record.created_at,
    )
    effects = _effects_with_verdict(
        Verdict(Outcome.PENDING, live=request),
        monkeypatch,
        tmp_path,
    )

    # When: the watch step asks for the posting receipt.
    receipt = effects.post_approval(record)

    # Then: adoption produces the same complete receipt contract as a fresh POST.
    assert receipt == ("adopted-message", "adopted-channel")


def test_probe_effect_with_an_unbound_channel_is_pending_without_touching_discord(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a corrupted persisted record with a message id but no approval channel.
    record = replace(
        _record(entry_text="operational reference worth relocating", message_id="msg-1"),
        channel_id="",
    )

    class _DiscordMustNotBeTouched:
        def __init__(self, *args: object) -> None:
            del args

        def probe(self, request: object) -> None:
            del request
            raise AssertionError("probe must not touch Discord for an unbound surface")

    monkeypatch.setattr(
        "automation.memory_relocate.effects_live.RelocateApprovalGate",
        _DiscordMustNotBeTouched,
    )
    effects = _effects_with_verdict(Verdict(Outcome.PENDING), monkeypatch, tmp_path)

    # When: the tick probes the persisted approval.
    result = effects.probe_reaction(record)

    # Then: an unbound surface is unverifiable and never reaches Discord.
    assert result == "pending"


AGENT_CHAT_CHANNEL_ID = "1528936606946122430"
_REQUEST_THREAD_BASE = 1528936606946122440


class _ThreadOpeningDirectory:
    """Fake channel directory that OPENS one thread per request, like the real one."""

    def __init__(self, token: str, owner_id: str, api: object, cache_path: Path) -> None:
        del token, owner_id, api, cache_path
        self.opened: list[str] = []

    def thread_id(self, index: int) -> str:
        """Discord answers a DISTINCT snowflake for every thread it creates."""
        return str(_REQUEST_THREAD_BASE + index)

    def agent_chat(self) -> str:
        return AGENT_CHAT_CHANNEL_ID

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        del kind
        raise AssertionError("a request must never fall back to the shared kind thread")

    def agent_chat_request_thread(self, kind: ApprovalKind, request: RequestThread) -> str:
        self.opened.append(request_thread_name(kind, request))
        return self.thread_id(len(self.opened) - 1)

    def describe(self, channel_id: str) -> ChannelFacts:
        for index, name in enumerate(self.opened):
            if channel_id == self.thread_id(index):
                return ChannelFacts(11, name, (), AGENT_CHAT_CHANNEL_ID)
        raise ApprovalSurfaceError(f"unknown channel: {channel_id}")


class _RecordingTransport:
    """Offline relocation transport: post, poll and delete without Discord."""

    def __init__(self, token: str, owner_id: str) -> None:
        del token
        self.owner_id = owner_id
        self.messages: dict[str, str] = {}
        self.channels: list[str] = []

    def api(self, method: str, path: str, payload: object = None) -> object:
        del payload
        raise AssertionError(f"the directory must not reach Discord: {method} {path}")

    def post_message(self, channel_id: str, content: str) -> str:
        message_id = f"msg-{len(self.channels) + 1}"
        self.channels.append(channel_id)
        self.messages[message_id] = content
        return message_id

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        del channel_id, message_id, emoji

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        del channel_id
        return self.messages.get(message_id)

    def get_reaction_users(
        self, channel_id: str, message_id: str, emoji: str
    ) -> tuple[tuple[str, bool], ...]:
        del channel_id, message_id, emoji
        return ()

    def delete_message(self, channel_id: str, message_id: str) -> None:
        del channel_id
        _ = self.messages.pop(message_id, None)


def test_re_requesting_the_same_relocation_reuses_its_live_request_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a relocation posted into its own approval thread by an earlier tick.
    module = "automation.memory_relocate.effects_live"
    record = _record(entry_text="operational reference worth relocating")
    path = tmp_path / "relocations.json"
    _save(path, record)
    key = record_key(record.source_kind, record.entry_sha256)
    directories: list[_ThreadOpeningDirectory] = []
    transports: list[_RecordingTransport] = []

    def make_transport(token: str, owner_id: str) -> _RecordingTransport:
        transports.append(_RecordingTransport(token, owner_id))
        return transports[-1]

    def make_directory(
        token: str, owner_id: str, api: object, cache_path: Path
    ) -> _ThreadOpeningDirectory:
        directories.append(_ThreadOpeningDirectory(token, owner_id, api, cache_path))
        return directories[-1]

    monkeypatch.setattr(
        f"{module}.load_config",
        lambda: ObsidianWriteConfig("git@example.invalid:owner/vault.git", tmp_path / "clone"),
    )
    monkeypatch.setattr(f"{module}.recover_entry_text", lambda memory_dir, current: "entry")
    monkeypatch.setattr(f"{module}.DiscordTransport", make_transport)
    monkeypatch.setattr(f"{module}.DiscordChannelDirectory", make_directory)
    effects = build_effects(
        memory_dir=tmp_path / "memory",
        state_path=path,
        rag_state_path=tmp_path / "rag-state.json",
        token="t",
        owner_id="9",
        now=datetime(2026, 7, 31, 14, 30, tzinfo=UTC),
    )
    directory, transport = directories[0], transports[0]

    # When: the next tick re-requests the very same record (same hash → PENDING).
    first = effects.post_approval(record)
    second = effects.post_approval(load_state(path).relocations[key])

    # Then: one approval key keeps ONE thread — no empty orphan per re-request,
    # every post landed in it, and the record still points at that first thread.
    assert directory.opened == [
        request_thread_name(ApprovalKind.OBSIDIAN_WRITE, RequestThread(title="relocated.md"))
    ]
    assert first == second == ("msg-1", directory.thread_id(0))
    assert transport.channels == [directory.thread_id(0)]
    assert load_state(path).relocations[key].approval_thread_id == directory.thread_id(0)


def test_post_after_a_missing_message_reuses_the_thread_the_request_opened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a request whose approval card went missing (the message binding was
    # cleared) but which already opened its own request thread.
    module = "automation.memory_relocate.effects_live"
    record = replace(
        _record(entry_text="operational reference worth relocating"),
        approval_thread_id="777888999",
    )
    _save(tmp_path / "relocations.json", record)
    candidates: list[tuple[object, ...]] = []
    specs: list[object] = []

    def fake_reuse_request_thread(
        kind: object, outstanding: object, directory: object, owner_id: str
    ) -> _StubBinding | None:
        del kind, directory, owner_id
        seen = tuple(outstanding)
        candidates.append(seen)
        return _StubBinding("777888999") if seen else None

    monkeypatch.setattr(f"{module}.reuse_request_thread", fake_reuse_request_thread)
    effects = _effects_with_verdict(
        Verdict(Outcome.POSTED, posted=PostedApproval("msg-1", "777888999")),
        monkeypatch,
        tmp_path,
        specs,
    )

    # When: the tick re-posts the approval request.
    assert effects.post_approval(record) == ("msg-1", "777888999")

    # Then: the record's own thread was offered as the reuse candidate...
    assert [candidate.channel_id for candidate in candidates[0]] == ["777888999"]
    # ...so no fresh binding was resolved — that path opens a SECOND thread for one
    # request, which the approval lifecycle forbids.
    assert specs == []
    stored = load_state(tmp_path / "relocations.json").relocations[
        record_key(record.source_kind, record.entry_sha256)
    ]
    assert stored.approval_thread_id == "777888999"

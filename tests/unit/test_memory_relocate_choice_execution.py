"""Real filesystem and cron integration of one frozen MOVE/KEEP/DELETE approval."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from automation.memory_curator.deletion import DeletionOutcome
from automation.memory_curator.model import MemoryKind
from automation.interop.approval_directory import DiscordChannelDirectory
from automation.memory_relocate.choice_accounts import Accounts
from automation.memory_relocate.choice_delete import NativeDelete
from automation.memory_relocate.choice_files import Mailbox
from automation.memory_relocate.choice_peer import PeerExecutor
from automation.memory_relocate.choice_request import ChoiceRequest, encode_request, parse_request
from automation.memory_relocate.choice_snapshot import SnapshotStore
from automation.memory_relocate.choice_verify import Verifier
from automation.memory_relocate.choices import ChoiceError, ROW_REACTIONS
from automation.memory_relocate.cron import memory_relocate_watch as watch
from automation.memory_relocate.model import RelocationState, record_key
from automation.memory_relocate.store import save_state
from automation.obsidian_write.config import ObsidianWriteConfig
from memory_choice_fakes import CHANNEL, NOW, OWNER, MessageSink, Reactions, members


@dataclass(frozen=True, slots=True)
class Execution:
    request: ChoiceRequest
    transport: Reactions
    verifier: Verifier
    peer: PeerExecutor
    agent_memory: Path
    peer_memory: Path
    notices: list[tuple[str, str]]


@pytest.fixture
def execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Execution:
    request = parse_request(encode_request(ChoiceRequest(members())))
    transport = Reactions(request.content)
    notices: list[tuple[str, str]] = []
    transport.users = {"✅": ((OWNER, False),), ROW_REACTIONS[0][2]: ((OWNER, False),),
                       ROW_REACTIONS[1][1]: ((OWNER, False),)}
    roots = tuple(tmp_path / role for role in ("agent-mailbox", "peer-mailbox"))
    for root in roots:
        root.mkdir(mode=0o750)
    participants = Accounts(*(Mailbox(root, os.getuid(), os.getgid()) for root in roots))
    memories = tuple(tmp_path / role for role in ("agent-memory", "peer-memory"))
    for memory in memories:
        memory.mkdir()
        (memory / "USER.md").write_text("\n§\n".join(text for _, text in request.members), encoding="utf-8")
        (memory / "MEMORY.md").write_text(request.members[0][1] + "\n§\n" + "Similar but different.", encoding="utf-8")
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": "9000"}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test")
    monkeypatch.setattr(watch, "_owner_id", lambda: OWNER)
    monkeypatch.setattr(watch, "STATE_PATH", tmp_path / "state" / "relocations.json")
    monkeypatch.setattr(watch, "MEMORY_DIR", memories[0])
    monkeypatch.setattr("automation.memory_relocate.effects_live.accounts", lambda: participants)
    monkeypatch.setattr("automation.memory_relocate.effects_live.DiscordTransport", lambda *args: transport)
    monkeypatch.setattr("automation.interop.reaction_approval.DiscordTransport", lambda *args: transport)
    monkeypatch.setattr("automation.memory_relocate.choice_notice.MessageTransport",
                        lambda token, channel_id: MessageSink(channel_id, notices))
    monkeypatch.setattr("automation.memory_relocate.effects_live.load_config",
                        lambda: ObsidianWriteConfig("git@example.invalid:owner/vault.git", tmp_path / "clone"))
    save_state(watch.STATE_PATH, RelocationState(1, {
        record_key(record.source_kind, record.entry_sha256): record for record, _ in request.members
    }))
    verifier = Verifier(transport, DiscordChannelDirectory("test", OWNER, transport.api))
    peer = PeerExecutor(SnapshotStore(tmp_path / "peer-decisions", NOW),
                        NativeDelete(memories[1], tmp_path / "peer-deletes", NOW), participants.peer)
    return Execution(request, transport, verifier, peer, *memories, notices)


def test_cron_when_peer_freezes_choices_moves_keeps_and_deletes_exact_entries(execution: Execution) -> None:
    # Given: the agent queued a card but may not execute its own apparent approval.
    pending = watch.run_once(NOW)
    assert {r.status for r in pending.state.relocations.values()} == {"posted"}
    execution.peer.run(execution.request, execution.verifier)

    # When: real cron ticks consume the peer receipt and execute through the native adapters.
    watch.run_once(NOW)
    watch.run_once(NOW)
    completed = watch.run_once(NOW)

    # Then: DELETE leaves both stores in both accounts, KEEP stays, MOVE uses the existing path.
    deleted, kept, moved = (text for _, text in execution.request.members)
    for memory in (execution.agent_memory, execution.peer_memory):
        assert deleted not in (memory / "USER.md").read_text()
        assert deleted not in (memory / "MEMORY.md").read_text()
        assert kept in (memory / "USER.md").read_text()
        assert len(tuple(memory.glob("*.deleted-*"))) >= 2
    assert moved not in (execution.agent_memory / "USER.md").read_text()
    assert moved in (execution.agent_memory / "MEMORY.md").read_text()
    rows = sorted(completed.state.relocations.values(), key=lambda r: r.batch_index or 0)
    assert [(r.choice, r.status) for r in rows] == [
        ("delete", "reconciled"), ("keep", "abandoned"), ("move", "reconciled"),
    ]
    assert [channel for channel, _ in execution.notices] == [CHANNEL]


def test_peer_when_card_is_missing_does_not_delete_even_with_agent_request(execution: Execution) -> None:
    # Given: a valid manifest and claimed approval, but peer's own GET cannot find the card.
    execution.transport.content = None
    before = (execution.peer_memory / "USER.md").read_bytes()
    # When / Then: independent verification fails before native writes.
    with pytest.raises(OSError):
        execution.peer.run(execution.request, execution.verifier)
    assert (execution.peer_memory / "USER.md").read_bytes() == before
    assert not tuple(execution.peer_memory.glob("*.deleted-*"))


def test_agent_when_owner_cancels_after_peer_freeze_stops_unfinished_effects(execution: Execution) -> None:
    # Given: peer finished, and the agent has durably recorded the original choices.
    watch.run_once(NOW)
    execution.peer.run(execution.request, execution.verifier)
    watch.run_once(NOW)
    before = (execution.agent_memory / "USER.md").read_bytes()
    execution.transport.users["⛔"] = ((OWNER, False),)
    # When: the next cron would normally execute MOVE and DELETE.
    result = watch.run_once(NOW)
    # Then: fresh agent-side cancellation checking prevents every remaining write.
    assert (execution.agent_memory / "USER.md").read_bytes() == before
    assert not result.reconciled and not result.written
    assert not tuple(execution.agent_memory.glob("*.deleted-*"))


def test_peer_when_entry_is_readded_after_receipt_never_replays_deletion(execution: Execution) -> None:
    # Given: a completed peer deletion and subsequently re-added identical text.
    execution.peer.run(execution.request, execution.verifier)
    restored = execution.request.members[0][1]
    (execution.peer_memory / "USER.md").write_text(restored, encoding="utf-8")
    # When: a duplicate request is processed.
    execution.peer.run(execution.request, execution.verifier)
    # Then: the old authorization cannot delete the new incarnation.
    assert (execution.peer_memory / "USER.md").read_text() == restored


def test_peer_when_crash_follows_deletion_refuses_destructive_retry(
    execution: Execution, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real delete succeeds but the caller crashes before recording completion.
    from automation.memory_relocate import choice_delete

    original = choice_delete.delete_entry

    def crash_after(
        memory_dir: Path, kind: MemoryKind, index: int, *, expected_bytes: bytes, now: datetime,
    ) -> DeletionOutcome:
        _ = original(memory_dir, kind, index, expected_bytes=expected_bytes, now=now)
        raise OSError("crash after mutation")

    monkeypatch.setattr(choice_delete, "delete_entry", crash_after)
    with pytest.raises(OSError):
        execution.peer.run(execution.request, execution.verifier)
    restored = execution.request.members[0][1]
    (execution.peer_memory / "USER.md").write_text(restored, encoding="utf-8")
    monkeypatch.setattr(choice_delete, "delete_entry", original)
    # When / Then: a surviving write-ahead intent prevents deleting re-added text.
    with pytest.raises(ChoiceError, match="delete-interrupted"):
        execution.peer.run(execution.request, execution.verifier)
    assert (execution.peer_memory / "USER.md").read_text() == restored

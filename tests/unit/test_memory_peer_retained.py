"""Retained peer execution stays request-bound after the agent schedule is removed."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.memory_relocate.choice_delete import NativeDelete
from automation.memory_relocate.choice_files import Mailbox
from automation.memory_relocate.choice_peer import PeerExecutor
from automation.memory_relocate.choice_relay import AgentDecision
from automation.memory_relocate.choice_request import ChoiceRequest, encode_request, parse_request
from automation.memory_relocate.choice_snapshot import Snapshot, SnapshotStore, encode_snapshot
from automation.memory_relocate.choices import Choice
from memory_choice_fakes import NOW, members


@dataclass(frozen=True, slots=True)
class Execution:
    request: ChoiceRequest
    reader: AgentDecision
    peer: PeerExecutor
    peer_memory: Path


@pytest.fixture
def execution(tmp_path: Path) -> Execution:
    request = parse_request(encode_request(ChoiceRequest(members())))
    agent = tmp_path / "agent"
    agent.mkdir(mode=0o750)
    peer = tmp_path / "peer"
    peer.mkdir(mode=0o750)
    memory = tmp_path / "memories"
    memory.mkdir()
    (memory / "USER.md").write_text("\n§\n".join(text for _, text in request.members), encoding="utf-8")
    reader = AgentDecision(Mailbox(agent, os.getuid(), os.getgid()))
    executor = PeerExecutor(SnapshotStore(tmp_path / "decisions", NOW),
                            NativeDelete(memory, tmp_path / "deletes", NOW),
                            Mailbox(peer, os.getuid(), os.getgid()))
    return Execution(request, reader, executor, memory)


def test_unanswered_request_does_not_delete(execution: Execution) -> None:
    before = (execution.peer_memory / "USER.md").read_bytes()
    result = execution.peer.run(execution.request, execution.reader)
    assert result is None
    assert (execution.peer_memory / "USER.md").read_bytes() == before


def test_completed_peer_deletion_cannot_delete_readded_text(execution: Execution) -> None:
    snapshot = Snapshot(execution.request, "approved",
                        (Choice.DELETE, Choice.KEEP, Choice.MOVE), NOW.isoformat())
    execution.reader.mailbox.publish(f"{execution.request.key}.decision.json", encode_snapshot(snapshot))
    execution.peer.run(execution.request, execution.reader)
    removed, kept, moved = (text for _, text in execution.request.members)
    remaining = (execution.peer_memory / "USER.md").read_text()
    assert removed not in remaining and kept in remaining and moved in remaining
    assert tuple(execution.peer_memory.glob("*.deleted-*"))
    (execution.peer_memory / "USER.md").write_text(removed, encoding="utf-8")
    execution.peer.run(execution.request, execution.reader)
    assert (execution.peer_memory / "USER.md").read_text() == removed

"""A per-line request the owner has not answered is waiting on the peer, not a fault.

Live 2026-10-15: memory-relocate-peer-watch exited 1 on 149 straight ticks with
reason=agent_decision_missing while the owner had not yet pressed the posted lines.
Kept in its own file so the per-line contract file stays as it was.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from automation.memory_relocate.choice_delete import NativeDelete
from automation.memory_relocate.choice_peer import PeerExecutor
from automation.memory_relocate.choice_request import ChoiceRequest, encode_request, parse_request
from automation.memory_relocate.choice_snapshot import SnapshotStore
from automation.memory_relocate.line_choices import build_lines
from memory_choice_fakes import NOW, members
from test_memory_peer_retained import Execution, execution as execution


def test_an_unanswered_line_leaves_the_peer_waiting_without_error_or_deletes(
    tmp_path: Path, execution: Execution,
) -> None:
    # Given: a posted line, no owner reaction, so the agent has published no decision.
    old, text = members(1)[0]
    record = build_lines(((old, text),))[0]
    message = "9003"
    request = parse_request(encode_request(ChoiceRequest(((replace(record, status="posted", message_id=message), text),))))
    peer_memory = tmp_path / "peer-memory"
    peer_memory.mkdir()
    (peer_memory / "USER.md").write_text(text, encoding="utf-8")
    peer = PeerExecutor(SnapshotStore(tmp_path / "peer-decisions", NOW),
                        NativeDelete(peer_memory, tmp_path / "peer-deletes", NOW), execution.peer.outbox)
    # When: the peer watcher ticks.
    outcome = peer.run(request, execution.reader)
    # Then: nothing is frozen, nothing is deleted, and no error ends the tick.
    assert outcome is None
    assert (peer_memory / "USER.md").read_text(encoding="utf-8") == text
    assert not tuple(peer_memory.glob("*.deleted-*"))
    assert not tuple((tmp_path / "peer-decisions").glob("*.json"))

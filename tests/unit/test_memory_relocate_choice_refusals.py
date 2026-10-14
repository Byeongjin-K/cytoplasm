"""Fail-closed peer authorization and cancellation across executor boundaries."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from automation.memory_relocate.choice_accounts import Accounts
from automation.memory_relocate.choice_files import Mailbox
from automation.memory_relocate.choice_request import ChoiceRequest
from automation.memory_relocate.choice_snapshot import encode_snapshot
from automation.memory_relocate.choices import ChoiceError
from automation.memory_relocate.cron import memory_relocate_peer_watch as peer_watch
from automation.memory_relocate.cron import memory_relocate_watch as watch
from memory_choice_fakes import NOW, OWNER
from test_memory_relocate_choice_execution import Execution, execution as execution


def test_peer_when_owner_has_not_approved_never_trusts_agent_manifest(execution: Execution) -> None:
    # Given: an agent supplied a valid manifest but no real owner approval exists.
    execution.transport.users.clear()
    before = (execution.peer_memory / "USER.md").read_bytes()
    watch.run_once(NOW)
    # When: peer evaluates the request without an agent decision.
    result = execution.peer.run(execution.request, execution.reader)
    # Then: neither an agent request nor the bot's pre-placed options authorize deletion.
    assert result is None
    assert (execution.peer_memory / "USER.md").read_bytes() == before
    assert not tuple(execution.peer_memory.glob("*.deleted-*"))


def test_peer_when_agent_decision_belongs_to_another_request_refuses(execution: Execution) -> None:
    # Given: a valid snapshot for a different physical approval card.
    watch.run_once(NOW)
    frozen = execution.agent.snapshots.load(execution.request)
    assert frozen is not None
    other = ChoiceRequest(tuple((replace(record, message_id="9010"), text)
                                for record, text in execution.request.members))
    execution.reader.mailbox.publish(f"{execution.request.key}.decision.json",
                                    encode_snapshot(replace(frozen, request=other)))
    before = (execution.peer_memory / "USER.md").read_bytes()
    # When / Then: a filename cannot rebind an agent decision to another request.
    with pytest.raises(ChoiceError, match="agent_decision_binding"):
        execution.peer.run(execution.request, execution.reader)
    assert (execution.peer_memory / "USER.md").read_bytes() == before


def test_peer_when_agent_observed_cancel_before_peer_keeps_withdrawal_terminal(execution: Execution) -> None:
    # Given: the agent saw cancellation before the peer's next tick.
    execution.transport.users["⛔"] = ((OWNER, False),)
    cancelled = watch.run_once(NOW)
    assert {r.status for r in cancelled.state.relocations.values()} == {"abandoned"}
    # An owner later removes the reaction; this does not revive a terminal request.
    execution.transport.users.pop("⛔")
    inbox = Mailbox(execution.peer.outbox.path.parent / "agent-mailbox", os.getuid(), os.getgid())
    withdrawn = inbox.read(f"{execution.request.key}.cancel.json") is not None
    before = (execution.peer_memory / "USER.md").read_bytes()
    # When: peer consumes the durable denial (never a grant of authority).
    result = execution.peer.run(execution.request, execution.reader, withdrawn=withdrawn)
    # Then: the old approval cannot become executable again.
    assert result is not None and result.cancelled
    assert (execution.peer_memory / "USER.md").read_bytes() == before


def test_peer_when_a_native_entry_is_ambiguous_refuses_without_deleting(execution: Execution) -> None:
    # Given: duplicate exact entries cannot be matched to a unique original.
    text = execution.request.members[0][1]
    (execution.peer_memory / "USER.md").write_text(f"{text}\n§\n{text}", encoding="utf-8")
    watch.run_once(NOW)
    # When / Then: no ambiguous deletion passes the native boundary.
    with pytest.raises(ChoiceError, match="delete_ambiguous"):
        execution.peer.run(execution.request, execution.reader)
    assert not tuple(execution.peer_memory.glob("*.deleted-*"))


def test_peer_cron_when_agent_decision_is_queued_uses_own_home_without_discord(
    execution: Execution, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real agent cron has published the request, and a separate peer home.
    watch.run_once(NOW)
    home = tmp_path / "peer-home"
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes/memories").symlink_to(execution.peer_memory, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    monkeypatch.setenv("INTEROP_CONFIG", str(tmp_path / "absent.json"))
    participants = Accounts(Mailbox(execution.peer.outbox.path.parent / "agent-mailbox",
                                    os.getuid(), os.getgid()), execution.peer.outbox)
    monkeypatch.setattr(peer_watch, "accounts", lambda: participants)
    # When: the deployed wrapper's complete tick runs, not only its pure state machine.
    code = peer_watch.run_once(NOW)
    # Then: only the peer home is changed and a peer-owned receipt is published.
    assert code == 0
    assert execution.request.members[0][1] not in (execution.peer_memory / "USER.md").read_text()
    assert execution.request.members[0][1] in (execution.agent_memory / "USER.md").read_text()
    assert execution.peer.outbox.read(f"{execution.request.key}.json") is not None


def test_peer_when_agent_republishes_cancel_stops_unfinished_deletes(execution: Execution) -> None:
    # Given: the agent froze DELETE but peer has not yet applied it.
    watch.run_once(NOW)
    frozen = execution.agent.snapshots.load(execution.request)
    assert frozen is not None
    execution.peer.snapshots.save(frozen)
    execution.transport.users["⛔"] = ((OWNER, False),)
    watch.run_once(NOW)
    assert execution.reader.decision(execution.request) == "cancelled"
    assert execution.reader.snapshot(execution.request) == replace(frozen, cancelled=True)
    before = (execution.peer_memory / "USER.md").read_bytes()
    # When: peer sees the cancelled decision, even without the separate withdrawal marker.
    result = execution.peer.run(execution.request, execution.reader)
    # Then: its existing snapshot cannot authorize unfinished deletes.
    assert result is not None and result.cancelled
    assert (execution.peer_memory / "USER.md").read_bytes() == before
    assert not tuple(execution.peer_memory.glob("*.deleted-*"))

"""Fail-closed peer authorization and cancellation across executor boundaries."""
from __future__ import annotations

import os
import json
from pathlib import Path

import pytest

from automation.interop.approval_surface import ApprovalSurfaceError
from automation.memory_relocate.choice_accounts import Accounts
from automation.memory_relocate.choice_files import Mailbox
from automation.memory_relocate.choices import ChoiceError
from automation.memory_relocate.cron import memory_relocate_peer_watch as peer_watch
from automation.memory_relocate.cron import memory_relocate_watch as watch
from memory_choice_fakes import NOW, OWNER
from test_memory_relocate_choice_execution import Execution, execution as execution


def test_peer_when_owner_has_not_approved_never_trusts_agent_manifest(execution: Execution) -> None:
    # Given: an agent supplied a valid manifest but no real owner approval exists.
    execution.transport.users.clear()
    before = (execution.peer_memory / "USER.md").read_bytes()
    # When: peer independently evaluates that request.
    result = execution.peer.run(execution.request, execution.verifier)
    # Then: neither an agent request nor the bot's pre-placed options authorize deletion.
    assert result is None
    assert (execution.peer_memory / "USER.md").read_bytes() == before
    assert not tuple(execution.peer_memory.glob("*.deleted-*"))


def test_peer_when_private_surface_config_is_missing_refuses(
    execution: Execution, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: peer has no independently configured agent-chat surface.
    monkeypatch.setenv("INTEROP_CONFIG", str(tmp_path / "absent.json"))
    before = (execution.peer_memory / "USER.md").read_bytes()
    # When / Then: owner reactions on an unverified surface cannot authorize deletion.
    with pytest.raises(ApprovalSurfaceError):
        execution.peer.run(execution.request, execution.verifier)
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
    result = execution.peer.run(execution.request, execution.verifier, withdrawn=withdrawn)
    # Then: the old approval cannot become executable again.
    assert result is not None and result.cancelled
    assert (execution.peer_memory / "USER.md").read_bytes() == before


def test_peer_when_a_native_entry_is_ambiguous_refuses_without_deleting(execution: Execution) -> None:
    # Given: duplicate exact entries cannot be matched to a unique original.
    text = execution.request.members[0][1]
    (execution.peer_memory / "USER.md").write_text(f"{text}\n§\n{text}", encoding="utf-8")
    # When / Then: no ambiguous deletion passes the native boundary.
    with pytest.raises(ChoiceError, match="delete_ambiguous"):
        execution.peer.run(execution.request, execution.verifier)
    assert not tuple(execution.peer_memory.glob("*.deleted-*"))


def test_peer_cron_when_request_is_queued_uses_own_home_and_credential_boundary(
    execution: Execution, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real agent cron has published the request, and a separate peer home.
    watch.run_once(NOW)
    home = tmp_path / "peer-home"
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes/memories").symlink_to(execution.peer_memory, target_is_directory=True)
    private = home / ".hermes/memory-relocate-peer"
    private.mkdir(mode=0o700)
    config = private / "approval-config.json"
    config.write_text(json.dumps({"agent_chat_channel_id": "9000"}), encoding="utf-8")
    config.chmod(0o600)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(peer_watch, "owner_id", lambda: OWNER)
    monkeypatch.setattr(peer_watch, "DiscordTransport", lambda *args: execution.transport)
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

"""New per-message checks; FS3-pinned files and legacy batch contracts stay untouched."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from automation.memory_relocate.choice_agent import AgentChoices
from automation.memory_relocate.choice_delete import NativeDelete
from automation.memory_relocate.choice_peer import PeerExecutor
from automation.memory_relocate.choice_relay import AgentDecision
from automation.memory_relocate.choice_request import ChoiceRequest, encode_request, parse_request
from automation.memory_relocate.choice_snapshot import SnapshotStore
from automation.memory_relocate.choice_verify import Verifier
from automation.memory_relocate.choices import Choice
from automation.memory_relocate.choices import ChoiceError
from automation.memory_relocate.cron import memory_relocate_watch as watch
from automation.memory_relocate.line_choices import REACTIONS, build_lines
from automation.memory_relocate.model import RelocationState, record_key
from automation.memory_relocate.relocation_store import RelocationStore
from automation.memory_relocate.render import prepare_approval_card
from automation.memory_relocate.store import save_state
from memory_choice_fakes import CHANNEL, NOW, OWNER, members
from relocate_line_fakes import Offline, offline_setup


@pytest.fixture
def line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Offline, ChoiceRequest, AgentChoices]:
    offline = offline_setup(tmp_path, monkeypatch)
    old, text = members(1)[0]
    record = build_lines(((old, text),))[0]
    message = offline.transport.post_message(CHANNEL, prepare_approval_card(record, text)[1])
    record = replace(record, status="posted", message_id=message)
    request = parse_request(encode_request(ChoiceRequest(((record, text),))))
    save_state(offline.state, RelocationState(1, {record_key(record.source_kind, record.entry_sha256): record}))
    (offline.memory / "USER.md").write_text(text, encoding="utf-8")
    (offline.memory / "MEMORY.md").write_text(text, encoding="utf-8")
    agent = AgentChoices(
        RelocationStore(offline.state), NativeDelete(offline.memory, tmp_path / "deletes", NOW),
        offline.accounts, Verifier(offline.transport, offline.directory),
        SnapshotStore(offline.state.parent / "choice-decisions", NOW),
    )
    return offline, request, agent


@pytest.mark.parametrize("emoji,choice,status", [
    ("➡️", Choice.MOVE, "approved"), ("📌", Choice.KEEP, "abandoned"),
    ("🗑️", Choice.DELETE, "approved"),
])
def test_single_owner_reaction_decides_immediately(line, emoji: str, choice: Choice, status: str) -> None:
    # Given: only one real owner reaction, no final approval.
    offline, request, _ = line
    offline.transport.reactors[(request.record.message_id, emoji)] = ((OWNER, False),)
    # When: the existing cron ticks.
    result = watch.run_once(NOW)
    # Then: exactly this choice is durable without ✅.
    record = next(iter(result.state.relocations.values()))
    assert (record.choice, record.status) == (choice, status)
    assert not {"✅", "⛔"} & set(offline.transport.reads)


def test_two_owner_reactions_keep(line) -> None:
    # Given: owner selects conflicting effects.
    offline, request, _ = line
    for emoji in ("➡️", "🗑️"):
        offline.transport.reactors[(request.record.message_id, emoji)] = ((OWNER, False),)
    # When: the cron freezes this line.
    result = watch.run_once(NOW)
    # Then: it is kept, with no native change.
    assert next(iter(result.state.relocations.values())).choice is Choice.KEEP
    assert (offline.memory / "USER.md").read_text() == request.members[0][1]


@pytest.mark.parametrize("users", [(("other", False),), ((OWNER, True),), ()])
def test_nonowner_bot_or_absent_reaction_stays_pending(line, users) -> None:
    # Given: nobody authorized this line.
    offline, request, _ = line
    offline.transport.reactors[(request.record.message_id, "🗑️")] = users
    # When: the cron examines it.
    result = watch.run_once(NOW)
    # Then: no choice or effect exists.
    record = next(iter(result.state.relocations.values()))
    assert (record.status, record.choice) == ("posted", None)
    assert not tuple(offline.accounts.agent.path.glob("*.decision.json"))


@pytest.mark.parametrize("failure", ["text", "missing", "identity", "native"])
def test_unverifiable_line_stays_pending(line, failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: owner chose DELETE, but its physical message or exact text is unverifiable.
    offline, request, _ = line
    message = request.record.message_id
    offline.transport.reactors[(message, "🗑️")] = ((OWNER, False),)
    if failure == "text":
        offline.transport.messages[message] = request.content.replace(request.members[0][1], "changed")
    elif failure == "missing":
        offline.transport.messages.pop(message)
    elif failure == "identity":
        original = offline.transport.api
        def wrong_id(method, path, payload=None):
            raw = original(method, path, payload)
            if isinstance(raw, dict) and "content" in raw:
                raw["id"] = "9999"
            return raw
        monkeypatch.setattr(offline.transport, "api", wrong_id)
    else:
        (offline.memory / "USER.md").write_text("changed native text", encoding="utf-8")
    before = (offline.memory / "USER.md").read_bytes()
    # When: the cron checks the line.
    result = watch.run_once(NOW)
    # Then: no new authorization exists and no memory changes.
    assert next(iter(result.state.relocations.values())).status == "posted"
    assert (offline.memory / "USER.md").read_bytes() == before
    assert not tuple(offline.accounts.agent.path.glob("*.decision.json"))


def test_removed_before_freeze_waits_and_frozen_choice_survives_restart(line) -> None:
    # Given: a reaction was added and removed before the watcher could freeze.
    offline, request, agent = line
    offline.transport.reactors[(request.record.message_id, "🗑️")] = ()
    assert agent.probe(request.record) == "pending"
    offline.transport.reactors[(request.record.message_id, "➡️")] = ((OWNER, False),)
    assert agent.probe(request.record) == "approved"
    offline.transport.reactors.clear()
    offline.transport.reactors[(request.record.message_id, "🗑️")] = ((OWNER, False),)
    # When: a restarted store receives changed reactions.
    restarted = replace(agent, snapshots=SnapshotStore(agent.snapshots.root, NOW))
    assert restarted.probe(request.record) == "approved"
    # Then: the first durable MOVE, not the later DELETE, is the authority.
    frozen = restarted.snapshots.load(request)
    assert frozen is not None and frozen.choices == (Choice.MOVE,)
    assert restarted.permits_move(request.record)


def test_line_delete_uses_tokenless_peer_and_backs_up_both_accounts(line, tmp_path: Path) -> None:
    # Given: a DELETE decision and a peer with no Discord dependency.
    offline, request, agent = line
    offline.transport.reactors[(request.record.message_id, "🗑️")] = ((OWNER, False),)
    assert agent.probe(request.record) == "approved"
    peer_memory = tmp_path / "peer-memory"
    peer_memory.mkdir()
    text = request.members[0][1]
    for kind in ("USER", "MEMORY"):
        (peer_memory / f"{kind}.md").write_text(text, encoding="utf-8")
    peer = PeerExecutor(SnapshotStore(tmp_path / "peer-decisions", NOW),
                        NativeDelete(peer_memory, tmp_path / "peer-deletes", NOW), offline.accounts.peer)
    assert not agent.delete(request.record).deleted
    # When: the tokenless relay executes first, then the agent consumes its bound receipt.
    peer.run(request, AgentDecision(offline.accounts.agent))
    outcome = agent.delete(request.record)
    # Then: exact entries are gone with each account's backup intact.
    assert outcome.deleted
    for memory in (peer_memory, offline.memory):
        assert len(tuple(memory.glob("*.deleted-*"))) == 2
        assert all((memory / f"{kind}.md").read_text() == "" for kind in ("USER", "MEMORY"))


def test_new_renderer_preserves_exact_multiline_text_and_destination() -> None:
    # Given: whitespace carries exact text identity.
    old, _ = members(1)[0]
    text = "original  \nline"
    record = build_lines(((old, text),))[0]
    # When: the new append-only card renders.
    version, content = prepare_approval_card(record, text)
    # Then: text and destination remain exact and the choice affordance is per-message.
    assert version == "mc-reloc-line-render-v1"
    assert f"\n{text}\n→ {record.note_relpath}\n" in content
    assert all(emoji in content for emoji in REACTIONS)


def test_manifest_text_hash_mismatch_refuses_before_effect(line) -> None:
    # Given: the exact line in the mailbox no longer matches the stored entry digest.
    _, request, _ = line
    forged = replace(request, members=((request.record, "substituted text"),))
    # When / Then: neither account can consume a changed-line manifest.
    with pytest.raises(ChoiceError, match="entry_digest"):
        parse_request(encode_request(forged))


def test_frozen_line_still_requires_readable_unchanged_message(line) -> None:
    # Given: MOVE has frozen, then the physical message is altered.
    offline, request, agent = line
    offline.transport.reactors[(request.record.message_id, "➡️")] = ((OWNER, False),)
    assert agent.probe(request.record) == "approved"
    offline.transport.messages[request.record.message_id] = "altered"
    # When / Then: durable choices do not bypass message integrity.
    with pytest.raises(ChoiceError, match="card_binding"):
        agent.permits_move(request.record)


def test_line_move_runs_existing_native_destination_gates(line) -> None:
    # Given: one owner MOVE and an initially empty destination.
    offline, request, _ = line
    (offline.memory / "MEMORY.md").write_text("", encoding="utf-8")
    offline.transport.reactors[(request.record.message_id, "➡️")] = ((OWNER, False),)
    # When: the existing cron authorizes, writes, verifies and reclaims the source.
    watch.run_once(NOW)
    written = watch.run_once(NOW)
    completed = watch.run_once(NOW)
    # Then: native removal follows verified storage, never the reaction alone.
    assert written.written and not written.reconciled
    assert completed.reconciled
    assert (offline.memory / "USER.md").read_text() == ""
    assert (offline.memory / "MEMORY.md").read_text() == request.members[0][1]
    assert tuple(offline.memory.glob("USER.md.deleted-*"))


def test_reaction_removed_during_snapshot_is_not_frozen(line, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: owner removes MOVE while the watcher is still collecting this line.
    offline, request, agent = line
    message = request.record.message_id
    offline.transport.reactors[(message, "➡️")] = ((OWNER, False),)
    original = offline.transport.get_reaction_users
    def removing(channel: str, identity: str, emoji: str):
        if emoji == "📌":
            offline.transport.reactors.pop((message, "➡️"), None)
        return original(channel, identity, emoji)
    monkeypatch.setattr(offline.transport, "get_reaction_users", removing)
    # When: the watcher completes its bounded snapshot.
    verdict = agent.probe(request.record)
    # Then: an intermediate reaction cannot authorize an effect after removal.
    assert verdict == "pending"
    assert agent.snapshots.load(request) is None

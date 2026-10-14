"""Independent owner verification and durable selection, separate from old v1 tests."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.memory_relocate.choice_request import ChoiceRequest, encode_request, parse_request
from automation.memory_relocate.choice_snapshot import SnapshotStore
from automation.memory_relocate.choices import Choice, ChoiceError, ROW_REACTIONS
from memory_choice_fakes import NOW, OWNER, OfflineVerifier, Reactions, members


@pytest.fixture(name="batch_request")
def request_fixture() -> ChoiceRequest:
    return parse_request(encode_request(ChoiceRequest(members())))


@pytest.fixture
def verifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, batch_request: ChoiceRequest) -> OfflineVerifier:
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": "9000"}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    transport = Reactions(batch_request.content)
    return OfflineVerifier(transport, DiscordChannelDirectory("test", OWNER, transport.api))


def test_freeze_when_choices_change_after_approval_uses_original_vector_after_restart(
    tmp_path: Path, batch_request: ChoiceRequest, verifier: OfflineVerifier,
) -> None:
    # Given: owner deletes row 1, keeps row 2 and leaves row 3 at the MOVE default.
    transport = verifier.transport
    transport.users = {"✅": ((OWNER, False),), ROW_REACTIONS[0][2]: ((OWNER, False),),
                       ROW_REACTIONS[1][1]: ((OWNER, False),)}
    first = SnapshotStore(tmp_path / "ledger", NOW).freeze(batch_request, verifier)
    assert first is not None
    transport.users = {"✅": ((OWNER, False),)}
    transport.reads.clear()

    # When: a restarted executor receives a duplicate request after reactions changed.
    frozen = SnapshotStore(tmp_path / "ledger", NOW).freeze(batch_request, verifier)

    # Then: there is no second reaction read and the authorized vector is unchanged.
    assert frozen is not None
    assert frozen.choices == (Choice.DELETE, Choice.KEEP, Choice.MOVE)
    assert transport.reads == []


@pytest.mark.parametrize("users", [(("someone", False),), ((OWNER, True),), ()])
def test_freeze_when_approve_is_not_owner_does_nothing(
    tmp_path: Path, batch_request: ChoiceRequest, verifier: OfflineVerifier, users: tuple[tuple[str, bool], ...],
) -> None:
    # Given: the final approve was not made by the real owner.
    verifier.transport.users = {"✅": users}
    # When: peer independently checks it.
    frozen = SnapshotStore(tmp_path / "ledger", NOW).freeze(batch_request, verifier)
    # Then: no executable snapshot exists.
    assert frozen is None
    assert not (tmp_path / "ledger").exists()


def test_freeze_when_cancel_and_approve_coexist_cancels_without_reading_rows(
    tmp_path: Path, batch_request: ChoiceRequest, verifier: OfflineVerifier,
) -> None:
    # Given: both final decisions are owner reactions.
    verifier.transport.users = {"✅": ((OWNER, False),), "⛔": ((OWNER, False),)}
    # When: the peer observes the card.
    frozen = SnapshotStore(tmp_path / "ledger", NOW).freeze(batch_request, verifier)
    # Then: cancellation wins, with no row snapshot.
    assert frozen is not None and frozen.phase == "cancelled"
    assert not set(verifier.transport.reads) & {emoji for row in ROW_REACTIONS for emoji in row}


@pytest.mark.parametrize("failure", ["missing", "edited", "content"])
def test_freeze_when_card_cannot_be_verified_never_authorizes(
    tmp_path: Path, batch_request: ChoiceRequest, verifier: OfflineVerifier, failure: str,
) -> None:
    # Given: the apparent approval refers to an absent or changed card.
    verifier.transport.users = {"✅": ((OWNER, False),)}
    if failure == "missing":
        verifier.transport.content = None
    elif failure == "edited":
        verifier.transport.edited = True
    else:
        verifier.transport.content = "different"
    # When / Then: independent verification fails before a snapshot exists.
    with pytest.raises((ChoiceError, HTTPError)):
        SnapshotStore(tmp_path / "ledger", NOW).freeze(batch_request, verifier)
    assert not (tmp_path / "ledger").exists()


def test_freeze_when_reaction_read_is_interrupted_refuses_to_resnapshot(
    tmp_path: Path, batch_request: ChoiceRequest, verifier: OfflineVerifier, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: approval is real but collection fails after the durable collecting marker.
    verifier.transport.users = {"✅": ((OWNER, False),)}
    original = verifier.transport.get_reaction_users

    def fail_row(channel: str, message: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        if emoji == ROW_REACTIONS[0][0]:
            raise OSError("offline")
        return original(channel, message, emoji)

    monkeypatch.setattr(verifier.transport, "get_reaction_users", fail_row)
    store = SnapshotStore(tmp_path / "ledger", NOW)
    with pytest.raises(OSError):
        store.freeze(batch_request, verifier)
    monkeypatch.setattr(verifier.transport, "get_reaction_users", original)

    # When / Then: restart cannot silently use a later selection.
    with pytest.raises(ChoiceError, match="snapshot-incomplete"):
        SnapshotStore(tmp_path / "ledger", NOW).freeze(batch_request, verifier)


def test_manifest_when_agent_changes_text_or_order_is_rejected(batch_request: ChoiceRequest) -> None:
    # Given: an agent-controlled manifest with a different source text.
    changed = replace(batch_request, members=((batch_request.record, "forged"), *batch_request.members[1:]))
    # When / Then: the peer recomputes hashes instead of trusting a supplied approval.
    with pytest.raises(ChoiceError, match="entry_digest"):
        parse_request(encode_request(changed))

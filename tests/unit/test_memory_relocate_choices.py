"""Owner choice routing and the bot's clickable affordances."""
from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from automation.memory_relocate.binding import batch_action_hash
from automation.memory_relocate.choices import Choice, ROW_REACTIONS, batch_choices_hash, read_choices
from automation.memory_relocate.render import prepare_batch_card
from memory_choice_fakes import OWNER, members


@pytest.mark.parametrize("users,expected", [
    (((OWNER, False),), Choice.DELETE),
    ((("other", False),), Choice.MOVE),
    (((OWNER, True),), Choice.MOVE),
    ((), Choice.MOVE),
])
def test_choice_when_reactors_vary_accepts_only_nonbot_owner(
    users: tuple[tuple[str, bool], ...], expected: Choice,
) -> None:
    # Given: only DELETE has these reactors.
    delete = ROW_REACTIONS[0][2]
    # When: the row is snapshotted.
    result = read_choices(lambda emoji: users if emoji == delete else (), OWNER, 1)
    # Then: neither the bot's seed nor another person authorizes deletion.
    assert result == (expected,)


def test_choice_when_owner_selects_multiple_options_keeps_entry() -> None:
    # Given: contradictory owner selections.
    reactions = set(ROW_REACTIONS[0])
    # When: the row is snapshotted.
    result = read_choices(lambda emoji: ((OWNER, False),) if emoji in reactions else (), OWNER, 1)
    # Then: the conservative conflict rule keeps the entry.
    assert result == (Choice.KEEP,)


def test_card_when_maximum_rows_stays_within_transport_budget() -> None:
    # Given / When: a five-row batch renders through the production renderer.
    _, content = prepare_batch_card(members(5))
    # Then: no chunking can split its approval binding.
    assert len(content) <= 1900


def test_hash_when_order_or_authorized_options_change_is_distinct() -> None:
    # Given: two distinct member hashes.
    hashes = ["sha256:one", "sha256:two"]
    # When: the new finite-choice proposal is bound.
    bound = batch_choices_hash(hashes)
    # Then: old MOVE-only approval and a different row order cannot authorize it.
    assert bound != batch_action_hash(hashes)
    assert bound != batch_choices_hash(list(reversed(hashes)))


def test_v1_when_replayed_keeps_original_numeric_keep_contract() -> None:
    # Given: an already stored v1 batch, not a newly constructed v2 card.
    batch = members(2)
    legacy_hash = batch_action_hash([r.action_hash for r, _ in batch])
    legacy = tuple((replace(r, batch_hash=legacy_hash, render_version="mc-reloc-batch-render-v1"), text)
                   for r, text in batch)
    # When: it is replayed through the public card API.
    version, content = prepare_batch_card(legacy)
    # Then: byte replay matches aa0c2b7's shipped renderer, independently evaluated
    # with this fixture. This is persisted approval equality, not a prose assertion.
    assert version == "mc-reloc-batch-render-v1"
    assert hashlib.sha256(content.encode()).hexdigest() == (
        "1282f99a026f242292d1d5fb8c7a0ca3318d7953bbd4c4d67f138a3dde78dfe2"
    )
    assert content != prepare_batch_card(batch)[1]

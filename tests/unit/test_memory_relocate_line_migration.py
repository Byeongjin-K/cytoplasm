"""Offline cron migration checks in a new file, leaving FS3-pinned tests untouched."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from automation.memory_relocate.cron import memory_relocate_watch as watch
from automation.memory_relocate.line_choices import REACTIONS, RENDER_VERSION, build_lines
from automation.memory_relocate.model import RelocationState, record_key
from automation.memory_relocate.render import prepare_batch_card
from automation.memory_relocate.store import load_state, save_state
from memory_choice_fakes import CHANNEL, NOW, OWNER, members
from relocate_line_fakes import Offline, offline_setup


def pending_batches(offline: Offline) -> tuple[RelocationState, tuple[str, ...]]:
    """Eleven pending lines on legacy cards, all on the existing request thread."""
    records = {}
    texts = []
    messages = []
    for number, count in enumerate((5, 5, 1)):
        batch = members(count)
        # Different group text identity, re-derive each plan through the real proposal path.
        from automation.memory_curator.binding import entry_digest
        from automation.memory_relocate.propose import build_batch, build_proposed_record, card_fits

        texts_in_batch = tuple(f"Synthetic group {number} reference {index}." for index in range(count))
        proposals = tuple(
            (build_proposed_record(
                text, source_kind="user", entry_sha256=entry_digest("user", text),
                reclaimable_chars=len(text), binding_kind="obsidian-write", binding_surface="agent-chat-thread",
                binding_channel_id=CHANNEL, binding_policy_version=8, now=NOW, destination="memory",
            ), text) for text in texts_in_batch
        )
        bound = build_batch(proposals, fits=card_fits)
        batch = tuple((replace(r, approval_thread_id=CHANNEL), text)
                      for r, text in zip(bound, texts_in_batch, strict=True))
        message = offline.transport.post_message(CHANNEL, prepare_batch_card(batch)[1])
        offline.transport.add_reaction(CHANNEL, message, "✅")
        offline.transport.add_reaction(CHANNEL, message, "⛔")
        messages.append(message)
        for record, text in batch:
            records[record_key(record.source_kind, record.entry_sha256)] = replace(
                record, status="posted", message_id=message,
            )
            texts.append(text)
    (offline.memory / "USER.md").write_text("\n§\n".join(texts), encoding="utf-8")
    state = RelocationState(1, records)
    save_state(offline.state, state)
    return state, tuple(messages)


def test_unreacted_batches_retire_and_repost_all_eleven_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: all eleven lines await owner input on three untouched batch cards.
    offline = offline_setup(tmp_path, monkeypatch)
    before, messages = pending_batches(offline)
    # When: the existing cron ticks once; no operator work or separate posting command.
    result = watch.run_once(NOW)
    # Then: all lines now have unique messages in the same thread, exactly three seeds each.
    records = tuple(result.state.relocations.values())
    assert len(result.posted) == 11
    assert {r.render_version for r in records} == {RENDER_VERSION}
    assert len({r.message_id for r in records}) == 11
    assert {r.approval_thread_id for r in records} == {CHANNEL}
    assert {r.channel_id for r in records} == {CHANNEL}
    assert not offline.transport.deleted
    assert all(message in offline.transport.messages for message in messages)
    for record in records:
        assert [emoji for message, emoji in offline.transport.seeds if message == record.message_id] == list(REACTIONS)
    archived = tuple((offline.state.parent / "superseded-batches").glob("*.json"))
    assert len(archived) == 3
    assert sum(len(load_state(path).relocations) for path in archived) == 11
    assert all(r.status == "abandoned" and r.last_block_reason == "superseded-by-per-line"
               for path in archived for r in load_state(path).relocations.values())
    assert {r.action_hash for r in records} == {r.action_hash for r in before.relocations.values()}
    # Exactly one short notice per old card, and none is repeated on the next tick.
    assert len(offline.transport.messages) == 17
    watch.run_once(NOW)
    assert len(offline.transport.messages) == 17


@pytest.mark.parametrize("emoji", ["✅", "⛔", "🟠", "unrelated"])
def test_migration_leaves_any_owner_reacted_batch_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, emoji: str,
) -> None:
    # Given: the first batch has any owner reaction, including a non-choice emoji.
    offline = offline_setup(tmp_path, monkeypatch)
    before, messages = pending_batches(offline)
    offline.transport.reactors[(messages[0], emoji)] = ((OWNER, False),)
    from automation.memory_relocate.line_migration import Migration
    from automation.memory_relocate.effects_live import recover_entry_text
    migration = Migration(offline.state, offline.memory, offline.transport, offline.directory, recover_entry_text)
    # When: the watcher migration checks pending cards.
    result = migration.run(before)
    # Then: the reacted batch binding remains byte-for-byte intact.
    original = {key: r for key, r in before.relocations.items() if r.message_id == messages[0]}
    assert {key: result.relocations[key] for key in original} == original
    assert not (offline.state.parent / "superseded-batches" / f"{messages[0]}.json").exists()
    assert not offline.transport.deleted


@pytest.mark.parametrize("users", [(("other", False),), ((OWNER, True),)])
def test_migration_ignores_nonowner_and_bot_reactors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, users,
) -> None:
    # Given: bot seeds or another person's reactions do not belong to the owner.
    offline = offline_setup(tmp_path, monkeypatch)
    _, messages = pending_batches(offline)
    offline.transport.reactors[(messages[0], "✅")] = users
    # When: cron automatically migrates the untouched request.
    result = watch.run_once(NOW)
    # Then: these reactions cannot strand any of the eleven lines.
    assert len(result.posted) == 11


def test_new_line_posts_use_the_existing_lifecycle_and_share_one_request_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: fresh line proposals have no thread yet.
    offline = offline_setup(tmp_path, monkeypatch)
    old = members(3)
    records = build_lines(old)
    save_state(offline.state, RelocationState(1, {
        record_key(r.source_kind, r.entry_sha256): replace(
            r, approval_thread_id=None, approval_guild_id=None, channel_id="",
        ) for r in records
    }))
    (offline.memory / "USER.md").write_text("\n§\n".join(text for _, text in old), encoding="utf-8")
    from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ApprovalSurface
    calls = []
    def binding(kind, directory, owner_id, *, request):
        calls.append(request)
        return ApprovalBinding(ApprovalKind.OBSIDIAN_WRITE, ApprovalSurface.AGENT_CHAT_THREAD, CHANNEL, 8, "9004")
    monkeypatch.setattr("automation.memory_relocate.effects_live.resolve_new_binding", binding)
    # When: cron posts all pending line proposals through request_approval.
    result = watch.run_once(NOW)
    # Then: only one request thread opens, with unique lifecycle keys and no batch message.
    assert len(calls) == 1 and len(result.posted) == 3
    assert len({r.message_id for r in result.state.relocations.values()}) == 3
    assert {r.approval_thread_id for r in result.state.relocations.values()} == {CHANNEL}


@pytest.mark.parametrize("reacted,expected_posts", [(True, 6), (False, 11)])
def test_migration_without_summary_still_reads_each_legacy_owner_reaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reacted: bool, expected_posts: int,
) -> None:
    # Given: message summary omits reactions; the per-emoji endpoint still has the truth.
    offline = offline_setup(tmp_path, monkeypatch)
    _, messages = pending_batches(offline)
    if reacted:
        offline.transport.reactors[(messages[0], "✅")] = ((OWNER, False),)
    original = offline.transport.api
    def without_summary(method, path, payload=None):
        raw = original(method, path, payload)
        if isinstance(raw, dict) and "content" in raw:
            raw.pop("reactions", None)
        return raw
    monkeypatch.setattr(offline.transport, "api", without_summary)
    # When: the existing watcher migrates legacy cards.
    result = watch.run_once(NOW)
    # Then: missing summary is never substituted for checking an owner's decision.
    assert len(result.posted) == expected_posts
    if reacted:
        assert not (offline.state.parent / "superseded-batches" / f"{messages[0]}.json").exists()

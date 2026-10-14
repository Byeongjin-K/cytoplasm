"""Destructive-boundary scenarios for memory-curator reconciliation and alerting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from automation.memory_curator.binding import PromotionReceipt, entry_digest
from automation.memory_curator.promotion import PromotionProposal, content_hash
from automation.memory_curator.state_store import load_state
from automation.memory_curator.watch import run_cycle

NOW: Final = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _memory_dir(tmp_path: Path, *, memory: str = "", user: str = "") -> Path:
    memories = tmp_path / "memories"
    memories.mkdir()
    _ = (memories / "MEMORY.md").write_text(memory, encoding="utf-8")
    _ = (memories / "USER.md").write_text(user, encoding="utf-8")
    return memories


def _poster(
    notes: dict[str, bytes], calls: list[PromotionProposal]
) -> Callable[[PromotionProposal], PromotionReceipt]:
    def promote(proposal: PromotionProposal) -> PromotionReceipt:
        calls.append(proposal)
        note_bytes = f"# Promoted\n\n{proposal.body}\n".encode()
        notes[proposal.slug] = note_bytes
        suffix = str(len(calls))
        return PromotionReceipt(
            draft_id=f"draft-{suffix}",
            confirm_message_id=f"message-{suffix}",
            slug=proposal.slug,
            note_sha256=hashlib.sha256(note_bytes).hexdigest(),
        )

    return promote


def test_s3_partial_approval_deletes_only_the_verified_entry(tmp_path: Path) -> None:
    # Given: two posted records but a persisted, hash-matching note for only the first promoted entry.
    first = "앞으로 첫 번째 원칙을 지킨다"
    second = "앞으로 두 번째 원칙을 지킨다"
    memories = _memory_dir(tmp_path, memory=f"{first}\n§\n{second}")
    state_path = tmp_path / "state.json"
    notes: dict[str, bytes] = {}
    calls: list[PromotionProposal] = []
    _ = run_cycle(
        memories, state_path, promote=_poster(notes, calls), alert=lambda _text: True,
        read_twin=lambda _slug: None, now=NOW,
    )
    before = (memories / "MEMORY.md").read_text(encoding="utf-8")

    # When: reconciliation can read only the first promoted artifact.
    result = run_cycle(
        memories, state_path, promote=_poster(notes, calls), alert=lambda _text: True,
        read_twin=lambda slug: notes.get(slug) if slug == calls[0].slug else None,
        now=NOW + timedelta(minutes=1),
    )

    # Then: exactly the artifact-bound entry is backed up and deleted; the other stays pending.
    after = (memories / "MEMORY.md").read_text(encoding="utf-8")
    state = load_state(state_path)
    assert len(result.deleted) == 1
    assert calls[0].entry_text not in after and calls[1].entry_text in after
    assert len(after) < len(before)
    assert len(list(memories.glob("MEMORY.md.deleted-*"))) == 1
    assert state.promotions[calls[0].promotion_key].status == "reconciled"
    assert state.promotions[calls[1].promotion_key].status == "posted"


def test_s4_rerun_after_delete_is_terminal_and_creates_no_second_backup(
    tmp_path: Path,
) -> None:
    # Given: one promotion has been posted and then reconciled into one native deletion.
    entry = "앞으로 삭제 멱등 원칙을 지킨다"
    memories = _memory_dir(tmp_path, memory=entry)
    state_path = tmp_path / "state.json"
    notes: dict[str, bytes] = {}
    calls: list[PromotionProposal] = []
    poster = _poster(notes, calls)
    _ = run_cycle(
        memories, state_path, promote=poster, alert=lambda _text: True,
        read_twin=lambda _slug: None, now=NOW,
    )
    _ = run_cycle(
        memories, state_path, promote=poster, alert=lambda _text: True,
        read_twin=notes.get, now=NOW + timedelta(minutes=1),
    )
    after_delete = (memories / "MEMORY.md").read_bytes()

    # When: another tick still sees the persisted twin note.
    result = run_cycle(
        memories, state_path, promote=poster, alert=lambda _text: True,
        read_twin=notes.get, now=NOW + timedelta(minutes=2),
    )

    # Then: reconciled state is terminal and neither bytes nor backups change again.
    assert result.deleted == ()
    assert (memories / "MEMORY.md").read_bytes() == after_delete
    assert len(list(memories.glob("MEMORY.md.deleted-*"))) == 1
    assert load_state(state_path).promotions[calls[0].promotion_key].status == "reconciled"


def test_s5_unchanged_actionable_state_alerts_once_across_three_ticks(
    tmp_path: Path,
) -> None:
    # Given: one unchanged near-cap bucket and a successful alert effect.
    memories = _memory_dir(tmp_path, user="x" * 2620)
    state_path = tmp_path / "state.json"
    alerts: list[str] = []

    # When: three ticks observe the same actionable signature.
    for offset in (timedelta(), timedelta(minutes=30), timedelta(hours=25)):
        _ = run_cycle(
            memories, state_path, promote=lambda _proposal: None,
            alert=lambda text: alerts.append(text) or True,
            read_twin=lambda _slug: None, now=NOW + offset,
        )

    # Then: change detection sends only on the first tick, even after cooldown.
    assert len(alerts) == 1


def test_s5_bucket_change_holds_inside_cooldown_then_sends_once(tmp_path: Path) -> None:
    # Given: a near bucket that has already produced one successful alert.
    memories = _memory_dir(tmp_path, user="x" * 2620)
    state_path = tmp_path / "state.json"
    alerts: list[str] = []

    def send(text: str) -> bool:
        alerts.append(text)
        return True

    _ = run_cycle(
        memories, state_path, promote=lambda _proposal: None, alert=send,
        read_twin=lambda _slug: None, now=NOW,
    )

    # When: the file enters critical inside cooldown and remains there past cooldown.
    _ = (memories / "USER.md").write_text("x" * 2880, encoding="utf-8")
    held = run_cycle(
        memories, state_path, promote=lambda _proposal: None, alert=send,
        read_twin=lambda _slug: None, now=NOW + timedelta(hours=1),
    )
    sent = run_cycle(
        memories, state_path, promote=lambda _proposal: None, alert=send,
        read_twin=lambda _slug: None, now=NOW + timedelta(hours=25),
    )

    # Then: no early send occurs and exactly one changed-state alert follows cooldown.
    assert held.alert_decision == "hold" and held.alerted is False
    assert sent.alert_decision == "send" and sent.alerted is True
    assert len(alerts) == 2


def test_hold_with_three_posted_promotions_sends_one_event_summary(tmp_path: Path) -> None:
    # Given: a near-cap alert was sent and three durable entries appear inside its cooldown.
    memories = _memory_dir(tmp_path, user="x" * 2620)
    state_path = tmp_path / "state.json"
    alerts: list[str] = []
    _ = run_cycle(
        memories,
        state_path,
        promote=lambda _proposal: None,
        alert=lambda text: alerts.append(text) or True,
        read_twin=lambda _slug: None,
        now=NOW,
        max_promotions=0,
    )
    prior_last_sent = load_state(state_path).alert.last_sent_signature
    alerts.clear()
    durable = "\n§\n".join(f"앞으로 원칙 {index}을 지킨다" for index in range(3))
    _ = (memories / "USER.md").write_text(f"{durable}\n§\n{'x' * 2445}", encoding="utf-8")
    notes: dict[str, bytes] = {}
    proposals: list[PromotionProposal] = []

    # When: the changed actionable signature is held but all promotions are posted.
    result = run_cycle(
        memories,
        state_path,
        promote=_poster(notes, proposals),
        alert=lambda text: alerts.append(text) or True,
        read_twin=lambda _slug: None,
        now=NOW + timedelta(hours=1),
    )

    # Then: one event-only DM explains all confirms without advancing near-cap last_sent.
    assert result.alert_decision == "hold"
    assert result.alerted is True
    assert len(proposals) == 3
    assert len(alerts) == 1
    assert all(f"저장 draft-{index}" in alerts[0] for index in range(1, 4))
    assert "⚠️" not in alerts[0]
    assert load_state(state_path).alert.last_sent_signature == prior_last_sent


def test_s5_failed_alert_does_not_advance_last_sent_and_retries(tmp_path: Path) -> None:
    # Given: an actionable state whose first DM send fails.
    memories = _memory_dir(tmp_path, user="x" * 2620)
    state_path = tmp_path / "state.json"
    attempts: list[str] = []

    def send(text: str) -> bool:
        attempts.append(text)
        return len(attempts) > 1

    # When: the same signature is observed on the following tick.
    first = run_cycle(
        memories, state_path, promote=lambda _proposal: None, alert=send,
        read_twin=lambda _slug: None, now=NOW,
    )
    after_failure = load_state(state_path).alert
    second = run_cycle(
        memories, state_path, promote=lambda _proposal: None, alert=send,
        read_twin=lambda _slug: None, now=NOW + timedelta(minutes=30),
    )

    # Then: failed delivery advances only observation and the next tick re-sends.
    assert first.alerted is False and after_failure.last_sent_signature is None
    assert second.alerted is True and len(attempts) == 2
    assert load_state(state_path).alert.last_sent_signature is not None


def test_v1_legacy_entries_are_neither_reproposed_nor_deleted(tmp_path: Path) -> None:
    # Given: eight durable entries covered only by the legacy normalized-hash state.
    entries = tuple(f"앞으로 legacy {index} 원칙을 지킨다" for index in range(8))
    memories = _memory_dir(tmp_path, memory="\n§\n".join(entries))
    state_path = tmp_path / "state.json"
    _ = state_path.write_text(
        json.dumps({"proposed": [content_hash(entry) for entry in entries]}), encoding="utf-8"
    )
    calls: list[PromotionProposal] = []

    # When: the v1 state is migrated during a normal cycle.
    result = run_cycle(
        memories, state_path, promote=lambda proposal: calls.append(proposal) or None,
        alert=lambda _text: True, read_twin=lambda _slug: None, now=NOW,
    )

    # Then: all eight remain audit-only and native memory is untouched.
    assert calls == [] and result.deleted == ()
    assert len(load_state(state_path).promotions) == 8
    assert {record.status for record in load_state(state_path).promotions.values()} == {
        "legacy_unbound"
    }
    assert (memories / "MEMORY.md").read_text(encoding="utf-8") == "\n§\n".join(entries)


def test_candidate_binding_uses_post_compaction_entry_bytes(tmp_path: Path) -> None:
    # Given: a durable entry whose raw file bytes have only surrounding whitespace drift.
    raw_entry = "  앞으로   배려를 원칙으로 한다  "
    canonical_entry = raw_entry.strip()
    memories = _memory_dir(tmp_path, memory=raw_entry)
    state_path = tmp_path / "state.json"
    notes: dict[str, bytes] = {}
    calls: list[PromotionProposal] = []
    poster = _poster(notes, calls)

    # When: compaction posts the canonical candidate and the next tick reconciles it.
    _ = run_cycle(
        memories, state_path, promote=poster, alert=lambda _text: True,
        read_twin=lambda _slug: None, now=NOW,
    )
    result = run_cycle(
        memories, state_path, promote=poster, alert=lambda _text: True,
        read_twin=notes.get, now=NOW + timedelta(minutes=1),
    )

    # Then: the note binding matches the post-compaction bytes and authorizes deletion.
    assert calls[0].entry_digest == entry_digest("memory", canonical_entry)
    assert calls[0].entry_digest != entry_digest("memory", raw_entry)
    assert len(result.deleted) == 1
    assert (memories / "MEMORY.md").read_text(encoding="utf-8") == ""

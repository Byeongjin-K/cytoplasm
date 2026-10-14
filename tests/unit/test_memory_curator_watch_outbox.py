from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from automation.memory_curator import state as state_module
from automation.memory_curator import watch as watch_module
from automation.memory_curator.alerting import ActionableState, signature
from automation.memory_curator.binding import PromotionReceipt
from automation.memory_curator.promotion import PromotionProposal, build_proposal
from automation.memory_curator.reporting import preview
from automation.memory_curator.state import AlertState, CuratorState, StateError
from automation.memory_curator.state_store import load_state, save_state

NOW: Final = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _memory_dir(tmp_path: Path, *, memory: str = "", user: str = "") -> Path:
    memories = tmp_path / "memories"
    memories.mkdir()
    _ = (memories / "MEMORY.md").write_text(memory, encoding="utf-8")
    _ = (memories / "USER.md").write_text(user, encoding="utf-8")
    return memories


def _poster(notes: dict[str, bytes], calls: list[PromotionProposal]):
    def promote(proposal: PromotionProposal) -> PromotionReceipt:
        calls.append(proposal)
        note_bytes = f"# Promoted\n\n{proposal.body}\n".encode()
        notes[proposal.slug] = note_bytes
        suffix = str(len(calls))
        return PromotionReceipt(
            f"draft-{suffix}",
            f"message-{suffix}",
            proposal.slug,
            hashlib.sha256(note_bytes).hexdigest(),
        )

    return promote


def _pending_posted(proposal: PromotionProposal, draft_id: str):
    return state_module.PendingOwnerEvent(
        f"{proposal.promotion_key}#posted",
        "posted",
        preview(proposal.entry_text),
        proposal.twin_kind,
        draft_id,
        None,
    )


def test_silent_with_completed_deletion_sends_one_event_summary(tmp_path: Path) -> None:
    # Given: a posted promotion whose post-deletion actionable signature is already observed.
    entry = "앞으로 삭제 설명 원칙을 지킨다"
    memories = _memory_dir(tmp_path, memory=entry)
    state_path = tmp_path / "state.json"
    notes: dict[str, bytes] = {}
    proposals: list[PromotionProposal] = []
    _ = watch_module.run_cycle(
        memories, state_path, promote=_poster(notes, proposals), alert=lambda _text: True,
        read_twin=lambda _slug: None, now=NOW,
    )
    quiet = signature(ActionableState({"memory": "ok", "user": "ok"}, {}, ()))
    state = load_state(state_path)
    save_state(state_path, replace(state, alert=AlertState(quiet, quiet, "2026-07-30T12:00:00Z", None)))
    alerts: list[str] = []

    # When: the approved twin is reconciled while decide_alert remains silent.
    result = watch_module.run_cycle(
        memories, state_path, promote=_poster(notes, proposals),
        alert=lambda text: alerts.append(text) or True, read_twin=notes.get,
        now=NOW + timedelta(minutes=1),
    )

    # Then: deletion is explained once without a near-cap line.
    assert result.alert_decision == "silent" and result.alerted is True
    assert len(alerts) == 1 and "삭제 완료(트윈 저장 검증 후): 1건" in alerts[0]
    assert "⚠️" not in alerts[0]


def test_send_with_promotions_and_near_cap_combines_one_dm(tmp_path: Path) -> None:
    # Given: three durable entries and enough filler to enter the near-cap bucket.
    durable = "\n§\n".join(f"앞으로 결합 원칙 {index}을 지킨다" for index in range(3))
    memories = _memory_dir(tmp_path, user=f"{durable}\n§\n{'x' * 2620}")
    state_path = tmp_path / "state.json"
    notes: dict[str, bytes] = {}
    proposals: list[PromotionProposal] = []
    alerts: list[str] = []

    def alert(text: str) -> bool:
        assert load_state(state_path).alert.last_sent_signature is None
        alerts.append(text)
        return True

    # When: the first actionable cycle posts promotions and sends successfully.
    result = watch_module.run_cycle(
        memories, state_path, promote=_poster(notes, proposals), alert=alert,
        read_twin=lambda _slug: None, now=NOW,
    )

    # Then: one DM carries event detail plus the warning, and last_sent advances afterward.
    assert result.alert_decision == "send" and result.alerted is True
    assert len(alerts) == 1 and alerts[0].count("저장 draft-") == 3
    assert "⚠️ 자체 메모리 근접" in alerts[0]
    assert load_state(state_path).alert.last_sent_signature is not None


def test_failed_event_send_retries_pending_once_then_clears(tmp_path: Path) -> None:
    # Given: one event-only promotion whose first summary send fails.
    memories = _memory_dir(tmp_path, memory="앞으로 재시도 원칙을 지킨다")
    state_path = tmp_path / "state.json"
    notes: dict[str, bytes] = {}
    proposals: list[PromotionProposal] = []
    attempts: list[str] = []

    def alert(text: str) -> bool:
        attempts.append(text)
        return len(attempts) > 1

    # When: a failed event tick is followed by two ticks with no fresh event.
    first = watch_module.run_cycle(
        memories, state_path, promote=_poster(notes, proposals), alert=alert,
        read_twin=lambda _slug: None, now=NOW,
    )
    pending_after_failure = load_state(state_path).pending_owner_events
    second = watch_module.run_cycle(
        memories, state_path, promote=_poster(notes, proposals), alert=alert,
        read_twin=lambda _slug: None, now=NOW + timedelta(minutes=1),
    )
    third = watch_module.run_cycle(
        memories, state_path, promote=_poster(notes, proposals), alert=alert,
        read_twin=lambda _slug: None, now=NOW + timedelta(minutes=2),
    )

    # Then: the durable event retries, clears on success, and never sends a third time.
    assert first.alerted is False and len(pending_after_failure) == 1
    assert second.alerted is True and third.alerted is False
    assert len(attempts) == 2 and load_state(state_path).pending_owner_events == {}


def test_same_owner_event_merged_twice_renders_once(tmp_path: Path) -> None:
    # Given: an identical pending event exists before the same promotion is freshly posted.
    entry = "앞으로 중복 병합 원칙을 지킨다"
    proposal = build_proposal(entry, source_kind="memory")
    event = _pending_posted(proposal, "draft-1")
    state_path = tmp_path / "state.json"
    save_state(state_path, CuratorState(3, {}, AlertState(None, None, None, None), {event.key: event}))
    alerts: list[str] = []

    # When: the current event is merged into the persisted outbox.
    _ = watch_module.run_cycle(
        _memory_dir(tmp_path, memory=entry), state_path, promote=_poster({}, []),
        alert=lambda text: alerts.append(text) or True, read_twin=lambda _slug: None, now=NOW,
    )

    # Then: promotion_key#phase deduplication renders one line.
    assert len(alerts) == 1 and alerts[0].count("저장 draft-1") == 1


def test_same_owner_event_key_with_changed_payload_fails_closed(tmp_path: Path) -> None:
    # Given: a pending key whose persisted draft differs from the fresh receipt.
    entry = "앞으로 충돌 거부 원칙을 지킨다"
    proposal = build_proposal(entry, source_kind="memory")
    event = _pending_posted(proposal, "draft-old")
    state_path = tmp_path / "state.json"
    save_state(state_path, CuratorState(3, {}, AlertState(None, None, None, None), {event.key: event}))

    # When / Then: the same key with a different payload raises before owner reporting.
    with pytest.raises(StateError, match="owner event payload changed"):
        _ = watch_module.run_cycle(
            _memory_dir(tmp_path, memory=entry), state_path, promote=_poster({}, []),
            alert=lambda _text: True, read_twin=lambda _slug: None, now=NOW,
        )


def test_posted_and_deleted_phases_for_one_promotion_report_independently(tmp_path: Path) -> None:
    # Given: a failed posted-event send and an approved twin ready for deletion.
    entry = "앞으로 단계 독립 원칙을 지킨다"
    memories = _memory_dir(tmp_path, memory=entry)
    state_path = tmp_path / "state.json"
    notes: dict[str, bytes] = {}
    proposals: list[PromotionProposal] = []
    _ = watch_module.run_cycle(
        memories, state_path, promote=_poster(notes, proposals), alert=lambda _text: False,
        read_twin=lambda _slug: None, now=NOW,
    )
    alerts: list[str] = []

    # When: deletion completes while the posted event remains pending.
    _ = watch_module.run_cycle(
        memories, state_path, promote=_poster(notes, proposals),
        alert=lambda text: alerts.append(text) or True, read_twin=notes.get,
        now=NOW + timedelta(minutes=1),
    )

    # Then: #posted and #deleted each render once and clear together.
    assert len(alerts) == 1
    assert alerts[0].count("저장 draft-1") == 1
    assert alerts[0].count("삭제 완료(트윈 저장 검증 후): 1건") == 1
    assert load_state(state_path).pending_owner_events == {}


def test_successful_event_tick_checkpoints_before_alert_then_saves_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an event-only promotion and an instrumented atomic state saver.
    memories = _memory_dir(tmp_path, memory="앞으로 체크포인트 원칙을 지킨다")
    state_path = tmp_path / "state.json"
    saves: list[CuratorState] = []
    real_save = save_state

    def tracked_save(path: Path, state: CuratorState) -> None:
        real_save(path, state)
        saves.append(state)

    monkeypatch.setattr("automation.memory_curator.watch.save_state", tracked_save)

    def alert(_text: str) -> bool:
        assert len(load_state(state_path).pending_owner_events) == 1
        return True

    # When: the owner DM succeeds.
    _ = watch_module.run_cycle(
        memories, state_path, promote=_poster({}, []), alert=alert,
        read_twin=lambda _slug: None, now=NOW,
    )

    # Then: durable pending state precedes alert, followed by one clearing save.
    assert len(saves) == 2
    assert len(saves[0].pending_owner_events) == 1
    assert saves[1].pending_owner_events == {}


def test_dry_run_creates_no_owner_events_and_never_calls_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a dry-run cycle with a durable candidate.
    memories = _memory_dir(tmp_path, memory="앞으로 드라이런 원칙을 지킨다")
    state_path = tmp_path / "state.json"
    calls: list[str] = []
    monkeypatch.setenv("MEMORY_CURATOR_DRY_RUN", "1")

    # When: the cycle computes intended work without applying it.
    result = watch_module.run_cycle(
        memories, state_path, promote=lambda _proposal: calls.append("promote"),
        alert=lambda _text: calls.append("alert") or True,
        read_twin=lambda _slug: None, now=NOW,
    )

    # Then: no external effect or outbox record is produced.
    assert result.promoted == () and calls == []
    assert load_state(state_path).pending_owner_events == {}

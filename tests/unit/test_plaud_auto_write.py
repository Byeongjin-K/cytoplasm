"""Auto mode must retain the frozen-body guard and the normal card path."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.plaud_sync import effects_live
from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.store import save_note_body, save_state
from automation.plaud_sync.watch_step import resolve_tick
from automation.obsidian_write.writer import WriteReceipt
from tests.unit.test_plaud_sync_effects import _BODY, _record, _notifier


@pytest.mark.parametrize("auto,tamper", [(True, False), (True, True), (False, False)])
def test_auto_write_or_normal_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auto: bool, tamper: bool,
) -> None:
    # Given: a frozen note, a release-written mode and observable effect boundaries.
    record = _record(status="planned", message_id=None, channel_id="", approval_thread_id=None)
    save_state(tmp_path / "plaud.json", PlaudSyncState(1, None, {"rec-001": record}))
    save_note_body(tmp_path, record.recording_id, _BODY + ("변조" if tamper else ""))
    monkeypatch.setattr("automation.auto_write.enabled", lambda kind, **kw: auto)
    writes: list[str] = []
    entries: list[tuple] = []
    posts: list[str] = []
    def write(plan, config, **kwargs):
        assert kwargs["authorize_push"](plan)
        writes.append(plan.body)
        return WriteReceipt(plan.relpath, "d" * 64, "origin/main", "c" * 40)
    monkeypatch.setattr(effects_live, "write_note", write)
    monkeypatch.setattr("automation.auto_write.record", lambda kind, entry: entries.append((kind, entry)))
    effects, _ = _notifier(tmp_path, monkeypatch)
    effects = replace(effects, post_approval=lambda r: posts.append(r.recording_id))
    # When: the real per-record FSM and live write adapter resolve.
    result = resolve_tick(PlaudSyncState(1, None, {"rec-001": record}), effects=effects)
    # Then: automatic writes never post; tampered bodies never reach the writer.
    assert len(writes) == int(auto and not tamper)
    assert len(entries) == len(writes)
    assert posts == ([] if auto else ["rec-001"])
    assert result.written == (("rec-001",) if auto and not tamper else ())
    if entries:
        assert entries[0][0] == "plaud"
        assert entries[0][1].revision == "c" * 40


def test_failed_auto_write_retries_without_approving_record(tmp_path, monkeypatch):
    # Given: an automatic write that failed before verification.
    from automation.plaud_sync.watch_step import ResolveEffects
    record = _record(status="planned", message_id=None)
    state = PlaudSyncState(1, None, {"rec-001": record})
    effects = ResolveEffects(lambda r: pytest.fail("card"), lambda r: "pending",
                             lambda r: None, lambda r, outcome: None,
                             datetime(2026, 10, 18, tzinfo=UTC), auto_write=True)
    # When: the attempt fails.
    result = resolve_tick(state, effects=effects)
    # Then: it remains planned (turning the switch off restores the card flow).
    assert result.state.records["rec-001"].status == "planned"
    assert not result.written

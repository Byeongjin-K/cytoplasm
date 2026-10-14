"""Thread continuity and explicit withdrawal recovery at the cron boundary."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.classify_model import EntryVerdict, Route
from automation.memory_relocate.cron import memory_relocate_watch as watch
from automation.memory_relocate.model import RelocationRecord, RelocationState, record_key
from automation.memory_relocate.propose import build_proposed_record
from automation.memory_relocate.watch_step import ResolveResult

NOW: Final = datetime(2026, 10, 14, tzinfo=UTC)
TEXT: Final = "Synthetic colleague prefers the title Researcher."


def _record() -> RelocationRecord:
    return build_proposed_record(
        TEXT, source_kind="user", entry_sha256=entry_digest("user", TEXT),
        reclaimable_chars=len(TEXT), binding_kind="obsidian-write",
        binding_surface="owner-dm", binding_channel_id="", binding_policy_version=8,
        now=NOW, destination="person",
    )


def test_binding_merge_when_only_thread_changed_preserves_every_batch_member() -> None:
    # Given: posting persisted thread IDs even though the message binding did not change.
    first = replace(_record(), batch_hash="batch", batch_index=1)
    second = replace(first, entry_sha256="b" * 64, batch_index=2)
    before = RelocationState(1, {record_key(r.source_kind, r.entry_sha256): r for r in (first, second)})
    persisted = replace(before, relocations={
        key: replace(record, approval_thread_id="thread", approval_guild_id="guild")
        for key, record in before.relocations.items()
    })
    result = ResolveResult(before, (), (), (), ())

    # When: the cron merges the effects' durable bindings before its final save.
    merged = watch._merge_effect_bindings(before, result, persisted)

    # Then: every member lends the same thread to a later re-post.
    assert {r.approval_thread_id for r in merged.state.relocations.values()} == {"thread"}


@pytest.mark.parametrize("route", ["PERSON_REFERENCE", "OPS_REFERENCE"])
@pytest.mark.parametrize("reason,expected", [
    ("owner-withdrew-approval", "proposed"), (None, "abandoned"), ("owner-kept", "abandoned"),
])
def test_discovery_when_abandoned_only_explicit_withdrawal_is_reproposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: Route,
    reason: str | None, expected: str,
) -> None:
    # Given: one already classified entry, abandoned for one specific reason.
    (tmp_path / "USER.md").write_text(TEXT, encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("", encoding="utf-8")
    record = replace(_record(), status="abandoned", last_block_reason=reason)
    key = record_key(record.source_kind, record.entry_sha256)
    state = RelocationState(1, {key: record})
    verdict = EntryVerdict("user", TEXT, route, "", "", None, True)
    monkeypatch.setattr(watch, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(watch, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("automation.memory_relocate.verdict_cache.classify_with_cache", lambda *a, **kw: (verdict,))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(tmp_path / "hermes"))

    # When: autonomous discovery considers the current state.
    result = watch._discover_and_propose(state, NOW)

    # Then: withdrawal replaces its old record, without reopening ordinary KEEP/cancel decisions.
    assert len(result.relocations) == 1
    assert result.relocations[key].status == expected
    if expected == "proposed":
        assert result.relocations[key].message_id is None
        assert result.relocations[key].last_block_reason is None

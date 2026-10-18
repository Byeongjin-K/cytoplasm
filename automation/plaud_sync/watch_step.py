"""Pure per-tick state machine driving one plaud lifelog record to its note."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Literal, TypeAlias, TypeGuard, assert_never

from .model import PlaudSyncRecord, PlaudSyncState

ReactionVerdict: TypeAlias = Literal["approved", "cancelled", "pending", "missing"]

_VERDICTS: Final = frozenset({"approved", "cancelled", "pending", "missing"})


def _is_verdict(value: str) -> TypeGuard[ReactionVerdict]:
    return value in _VERDICTS


@dataclass(frozen=True, slots=True)
class ResolveEffects:
    post_approval: Callable[[PlaudSyncRecord], tuple[str, str] | None]
    probe_reaction: Callable[[PlaudSyncRecord], str]
    write_obsidian: Callable[[PlaudSyncRecord], tuple[str, str] | None]
    notify_result: Callable[[PlaudSyncRecord, str], None]
    now: datetime
    auto_write: bool = False


@dataclass(frozen=True, slots=True)
class ResolveResult:
    state: PlaudSyncState
    posted: tuple[str, ...]
    written: tuple[str, ...]
    abandoned: tuple[str, ...]


def _write_approved(
    records: dict[str, PlaudSyncRecord],
    key: str,
    effects: ResolveEffects,
    written: list[str],
    transition_time: str,
) -> None:
    """Push one approved note; a failed write leaves it ``approved`` for the next tick."""
    record = records[key]
    receipt = effects.write_obsidian(record)
    if receipt is None:
        return
    remote_ref, note_content_sha256 = receipt
    records[key] = replace(
        record,
        status="written",
        last_block_reason=None,
        remote_ref=remote_ref,
        note_content_sha256=note_content_sha256,
        written_at=transition_time,
    )
    written.append(key)
    effects.notify_result(records[key], "written")


def resolve_tick(
    state: PlaudSyncState,
    *,
    effects: ResolveEffects,
    max_posts: int = 3,
) -> ResolveResult:
    records = dict(state.records)
    posted: list[str] = []
    written: list[str] = []
    abandoned: list[str] = []
    transition_time = effects.now.isoformat()

    for key in sorted(records):
        record = records[key]
        match record.status:
            case "planned":
                if effects.auto_write and record.message_id is None:
                    _write_approved(records, key, effects, written, transition_time)
                    continue
                if len(posted) >= max_posts:
                    continue
                receipt = effects.post_approval(record)
                if receipt is None:
                    continue
                message_id, channel_id = receipt
                if message_id and channel_id:
                    records[key] = replace(
                        record,
                        status="posted",
                        message_id=message_id,
                        channel_id=channel_id,
                    )
                    posted.append(key)

            case "posted":
                verdict = effects.probe_reaction(record)
                if not _is_verdict(verdict):
                    continue
                match verdict:
                    case "approved":
                        records[key] = replace(
                            record, status="approved", approved_at=transition_time
                        )
                        # Write in the tick that read the ✅ — waiting for the next
                        # tick cost up to 20 minutes between approval and vault save.
                        _write_approved(records, key, effects, written, transition_time)
                    case "cancelled":
                        records[key] = replace(record, status="abandoned")
                        abandoned.append(key)
                        effects.notify_result(records[key], "abandoned")
                    case "missing":
                        records[key] = replace(record, status="planned", message_id=None)
                    case "pending":
                        pass
                    case unreachable:
                        assert_never(unreachable)

            case "approved":
                _write_approved(records, key, effects, written, transition_time)

            case "transcribing" | "written" | "abandoned":
                pass
            case unreachable:
                assert_never(unreachable)

    return ResolveResult(
        state=PlaudSyncState(state.version, state.last_poll_at, records),
        posted=tuple(posted),
        written=tuple(written),
        abandoned=tuple(abandoned),
    )

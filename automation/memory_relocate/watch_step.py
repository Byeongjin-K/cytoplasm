from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Literal, TypeAlias, TypeGuard, assert_never

from .apply import ApplyOutcome
from .choices import Choice, RENDER_VERSION as CHOICES_VERSION
from .model import RelocationRecord, RelocationState

RuntimeRelocationStatus: TypeAlias = Literal[
    "proposed",
    "posted",
    "approved",
    "written",
    "ingested",
    "reconciled",
    "abandoned",
]
ReactionVerdict: TypeAlias = Literal["approved", "cancelled", "pending", "missing", "kept"]

_RUNTIME_STATUSES: Final = frozenset(
    {"proposed", "posted", "approved", "written", "ingested", "reconciled", "abandoned"}
)
_REACTION_VERDICTS: Final = frozenset({"approved", "cancelled", "pending", "missing", "kept"})


def _is_runtime_status(value: str) -> TypeGuard[RuntimeRelocationStatus]:
    return value in _RUNTIME_STATUSES


def _is_reaction_verdict(value: str) -> TypeGuard[ReactionVerdict]:
    return value in _REACTION_VERDICTS


@dataclass(frozen=True, slots=True)
class ResolveEffects:
    post_approval: Callable[[RelocationRecord], tuple[str, str] | None]
    probe_reaction: Callable[[RelocationRecord], str]
    write_obsidian: Callable[[RelocationRecord], tuple[str, str] | None]
    verify_rag: Callable[[RelocationRecord], bool]
    apply_delete: Callable[[RelocationRecord], ApplyOutcome]
    now: datetime


@dataclass(frozen=True, slots=True)
class ResolveResult:
    state: RelocationState
    posted: tuple[str, ...]
    written: tuple[str, ...]
    reconciled: tuple[str, ...]
    abandoned: tuple[str, ...]


def resolve_tick(
    state: RelocationState,
    *,
    effects: ResolveEffects,
    max_posts: int = 1,
) -> ResolveResult:
    relocations = dict(state.relocations)
    posted: list[str] = []
    written: list[str] = []
    reconciled: list[str] = []
    abandoned: list[str] = []
    transition_time = effects.now.isoformat()
    touched: set[str] = set()

    def batch_members(record: RelocationRecord, status: str) -> list[str]:
        if record.batch_hash is None:
            return []
        return [
            other_key
            for other_key, other in relocations.items()
            if other.batch_hash == record.batch_hash and other.status == status
        ]

    for key in sorted(relocations):
        if key in touched:
            continue
        record = relocations[key]
        status = str(record.status)
        if status.startswith("legacy") or not _is_runtime_status(status):
            continue

        if record.render_version == CHOICES_VERSION and status in ("approved", "written"):
            current = effects.probe_reaction(record)
            if current in ("cancelled", "kept"):
                relocations[key] = replace(record, status="abandoned", last_block_reason="owner-cancelled")
                abandoned.append(key)
                continue
            if current != "approved":
                continue

        match status:
            case "proposed":
                if len(posted) >= max_posts:
                    continue
                receipt = effects.post_approval(record)
                if receipt is None:
                    continue
                message_id, channel_id = receipt
                if message_id and channel_id:
                    # One batch card posts once and binds every member of the batch.
                    for member_key in batch_members(record, "proposed") or [key]:
                        relocations[member_key] = replace(
                            relocations[member_key],
                            status="posted",
                            message_id=message_id,
                            channel_id=channel_id,
                        )
                        touched.add(member_key)
                    posted.append(key)

            case "posted":
                verdict = effects.probe_reaction(record)
                if not _is_reaction_verdict(verdict):
                    continue
                match verdict:
                    case "approved":
                        relocations[key] = replace(
                            record,
                            status="approved",
                            approved_at=transition_time,
                        )
                    case "cancelled":
                        relocations[key] = replace(record, status="abandoned")
                        abandoned.append(key)
                    case "kept":
                        relocations[key] = replace(record, status="abandoned", last_block_reason="owner-kept")
                        abandoned.append(key)
                    case "missing":
                        relocations[key] = replace(
                            record,
                            status="proposed",
                            message_id=None,
                        )
                    case "pending":
                        pass
                    case unreachable:
                        assert_never(unreachable)

            case "approved":
                match record.choice:
                    case Choice.DELETE:
                        outcome = effects.apply_delete(record)
                        if outcome.deleted:
                            relocations[key] = replace(
                                record, status="reconciled", reconciled_at=transition_time,
                                backup_path=outcome.backup_path, last_block_reason=None,
                            )
                            reconciled.append(key)
                        else:
                            relocations[key] = replace(record, last_block_reason=outcome.reason)
                        continue
                    case Choice.KEEP:
                        continue
                    case Choice.MOVE | None:
                        pass
                    case _:
                        assert_never(record.choice)
                write_receipt = effects.write_obsidian(record)
                if write_receipt is not None:
                    remote_ref, note_content_sha256 = write_receipt
                    relocations[key] = replace(
                        record,
                        status="written",
                        remote_ref=remote_ref,
                        note_content_sha256=note_content_sha256,
                        written_at=transition_time,
                    )
                    written.append(key)

            case "written":
                if effects.verify_rag(record):
                    outcome = effects.apply_delete(record)
                    if outcome.deleted:
                        relocations[key] = replace(
                            record,
                            status="reconciled",
                            reconciled_at=transition_time,
                            backup_path=outcome.backup_path,
                            last_block_reason=None,
                        )
                        reconciled.append(key)
                    else:
                        relocations[key] = replace(
                            record,
                            last_block_reason=outcome.reason,
                        )

            case "ingested" | "reconciled" | "abandoned":
                pass
            case unreachable:
                assert_never(unreachable)

    return ResolveResult(
        state=RelocationState(version=state.version, relocations=relocations),
        posted=tuple(posted),
        written=tuple(written),
        reconciled=tuple(reconciled),
        abandoned=tuple(abandoned),
    )

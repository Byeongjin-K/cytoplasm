from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime

from .binding import RelocationHashFields, batch_action_hash, relocation_action_hash
from .model import MemoryKind, RelocationRecord
from .plan import build_relocation_plan
from .rag_verify import rag_source_key
from .render import RenderError, prepare_batch_card


def build_proposed_record(
    entry_text: str,
    *,
    source_kind: MemoryKind,
    entry_sha256: str,
    reclaimable_chars: int,
    binding_kind: str,
    binding_surface: str,
    binding_channel_id: str,
    binding_policy_version: int,
    now: datetime,
    destination: str | None = None,
) -> RelocationRecord:
    plan = build_relocation_plan(entry_text, source_kind=source_kind, destination=destination)
    note_relpath = plan.note_plan.relpath.as_posix()
    action_hash = relocation_action_hash(
        RelocationHashFields(source_kind, entry_sha256, note_relpath, plan.note_plan_sha256, destination)
    )
    return RelocationRecord(
        version=1,
        source_kind=source_kind,
        entry_sha256=entry_sha256,
        note_relpath=note_relpath,
        note_plan_sha256=plan.note_plan_sha256,
        reclaimable_chars=reclaimable_chars,
        action_hash=action_hash,
        status="proposed",
        kind=binding_kind,
        surface=binding_surface,
        channel_id=binding_channel_id,
        policy_version=binding_policy_version,
        message_id=None,
        created_at=now.isoformat(),
        approved_at=None,
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=None,
        rag_source_key=None if destination == "memory" else rag_source_key(note_relpath),
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
        destination=destination,
    )


#: Number reactions 1️⃣…🔟 let the owner drop single lines, so a card holds at most ten.
MAX_BATCH = 10


def build_batch(
    members: Sequence[tuple[RelocationRecord, str]],
    *,
    fits: Callable[[Sequence[tuple[RelocationRecord, str]]], bool],
) -> tuple[RelocationRecord, ...]:
    """Take members in order while one card still fits (at most ``MAX_BATCH``), bind them with one hash.

    A member that does not fit is skipped, not a wall: an entry longer than a card would otherwise
    stop every later one. The rest waits for the next cycle (the watcher proposes only when idle).
    """
    taken: list[tuple[RelocationRecord, str]] = []
    for member in members:
        if len(taken) < MAX_BATCH and fits([*taken, member]):
            taken.append(member)
    if not taken:
        return ()
    return _bound(taken)


def card_fits(members: Sequence[tuple[RelocationRecord, str]]) -> bool:
    """Whether one batch card for these members renders within the postable length."""
    bound = _bound(members)
    try:
        _ = prepare_batch_card(list(zip(bound, (text for _, text in members), strict=True)))
    except RenderError:
        return False
    return True


def _bound(members: Sequence[tuple[RelocationRecord, str]]) -> tuple[RelocationRecord, ...]:
    batch = batch_action_hash([record.action_hash for record, _ in members])
    return tuple(
        replace(record, batch_hash=batch, batch_index=number)
        for number, (record, _) in enumerate(members, start=1)
    )

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.deletion import DeletionError, delete_entry
from automation.memory_curator.watch_steps import read_native

from .model import RelocationRecord

GateBlock = Literal[
    "not_approved",
    "action_hash_drift",
    "obsidian_not_written",
    "rag_not_ingested",
    "entry_absent",
    "entry_ambiguous",
    "peer_pending",
]


@dataclass(frozen=True, slots=True)
class ApplyOutcome:
    deleted: bool
    reason: GateBlock | None
    backup_path: str | None
    freed_chars: int


@dataclass(frozen=True, slots=True)
class ApplyDeps:
    memory_dir: Path
    read_twin: Callable[[str], bytes | None]
    verify_rag: Callable[[str, str], bool]
    recompute_action_hash: Callable[[RelocationRecord], str]
    now: datetime


def apply_relocation(
    record: RelocationRecord,
    note_body: str,
    *,
    deps: ApplyDeps,
) -> ApplyOutcome:
    # The driver runs apply on a "written" record (obsidian note already pushed); a record only
    # reaches "written" after the owner's ✅ (posted→approved→written). The security check is the
    # composite action-hash match, not the label — so accept "written" (and "approved" for a direct call).
    if record.status not in ("approved", "written"):
        return _blocked("not_approved")

    if deps.recompute_action_hash(record) != record.action_hash:
        return _blocked("action_hash_drift")

    note_bytes = deps.read_twin(record.note_relpath)
    if note_bytes is None:
        return _blocked("obsidian_not_written")
    if (
        record.note_content_sha256 is not None
        and hashlib.sha256(note_bytes).hexdigest() != record.note_content_sha256
    ):
        return _blocked("obsidian_not_written")

    if not deps.verify_rag(record.note_relpath, note_body):
        return _blocked("rag_not_ingested")

    current_bytes, memory_file = read_native(deps.memory_dir, record.source_kind)
    matching_indices = [
        index
        for index, entry in enumerate(memory_file.entries)
        if entry_digest(record.source_kind, entry.text) == record.entry_sha256
    ]
    if not matching_indices:
        return _blocked("entry_absent")
    if len(matching_indices) > 1:
        return _blocked("entry_ambiguous")

    try:
        outcome = delete_entry(
            deps.memory_dir,
            record.source_kind,
            matching_indices[0],
            expected_bytes=current_bytes,
            now=deps.now,
        )
    except DeletionError:
        return _blocked("entry_absent")
    return ApplyOutcome(
        deleted=True,
        reason=None,
        backup_path=str(outcome.backup_path),
        freed_chars=outcome.before_chars - outcome.after_chars,
    )


def _blocked(reason: GateBlock) -> ApplyOutcome:
    return ApplyOutcome(False, reason, None, 0)

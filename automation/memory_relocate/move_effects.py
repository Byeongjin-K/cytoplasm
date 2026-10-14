"""Existing MOVE effects: verified destination write before native reclamation."""
from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final

from automation.interop.external_effect_gate import ApprovalContext
from automation.interop.reaction_approval import record_push_approval
from automation.memory_curator.deletion import DeletionError, append_entry
from automation.memory_curator.model import CAPS
from automation.memory_curator.watch_steps import read_native
from automation.obsidian_write import gate_binding
from automation.obsidian_write.config import ObsidianWriteConfig, ObsidianWriteError
from automation.obsidian_write.writer import write_note

from .apply import ApplyDeps, ApplyOutcome, apply_relocation
from .binding import RelocationHashFields, relocation_action_hash
from .model import RelocationRecord
from .plan import RelocationPlan, build_relocation_plan
from .rag_verify import verify_ingested

_EFFECT_ERRORS: Final = (OSError, ValueError, RuntimeError, ObsidianWriteError)

@dataclass(frozen=True, slots=True)
class MoveContext:
    memory_dir: Path
    rag_state_path: Path
    write_config: ObsidianWriteConfig
    push_approval_log: Path
    owner_id: str
    now: datetime


@dataclass(frozen=True, slots=True)
class MoveEffects:
    write: Callable[[RelocationRecord], tuple[str, str] | None]
    verify: Callable[[RelocationRecord], bool]
    apply: Callable[[RelocationRecord], ApplyOutcome]


def _safe_read_note(clone_dir: Path, note_relpath: str) -> bytes | None:
    relpath = PurePosixPath(note_relpath)
    if relpath.is_absolute() or ".." in relpath.parts:
        return None
    try:
        descriptor = os.open(clone_dir / Path(relpath), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    except OSError:
        return None
    finally:
        os.close(descriptor)


def build_move_effects(
    context: MoveContext, recover_entry_text: Callable[[Path, RelocationRecord], str | None],
) -> MoveEffects:
    memory_dir, rag_state_path = context.memory_dir, context.rag_state_path
    write_config, push_approval_log = context.write_config, context.push_approval_log
    owner_id, now = context.owner_id, context.now

    def plan_for(record: RelocationRecord) -> RelocationPlan | None:
        try:
            text = recover_entry_text(memory_dir, record)
            return None if text is None else build_relocation_plan(
                text, source_kind=record.source_kind, destination=record.destination
            )
        except _EFFECT_ERRORS:
            return None

    def write(record: RelocationRecord) -> tuple[str, str] | None:
        if record.destination == "memory":
            return append_rule(record)
        plan = plan_for(record)
        if plan is None:
            return None
        # The push is a denylisted mutation: the gate needs the owner approval for THIS tool call.
        # We are only here because the approval gate probed a real owner ✅ on the bound message,
        # and that ✅ approved this exact note (the composite hash binds relpath+title+body), so
        # transcribe it once into the gate's log before pushing.
        context = ApprovalContext(
            approval_log=push_approval_log, owner_id=owner_id, e2e_test_mode=False
        )
        try:
            decision = gate_binding.evaluate(plan.note_plan, context=context)
            if not decision.allowed and record.message_id:
                record_push_approval(
                    push_approval_log,
                    action_hash=decision.action_hash,
                    target_id=decision.target_id,
                    owner_id=owner_id,
                    message_id=record.message_id,
                    now=now,
                )
            receipt = write_note(plan.note_plan, write_config, approval_context=context)
        except _EFFECT_ERRORS:
            return None
        return receipt.remote_ref, receipt.content_sha256

    def note_text(record: RelocationRecord) -> str | None:
        """The note content the RAG ingester actually indexed — the RENDERED file, not the plan.

        ``render_note`` adds the title/callout (and dated lines) at write time, so the plan body
        alone never reproduces the ingested document's fingerprint.  Read the pushed note back
        from the write clone instead.
        """
        raw = _safe_read_note(write_config.clone_dir, record.note_relpath)
        return None if raw is None else raw.decode("utf-8", errors="replace")

    def verified(record: RelocationRecord) -> bool:
        if record.destination == "memory":
            return native_rule(record) is not None
        try:
            body = note_text(record)
            return (
                False
                if body is None
                else verify_ingested(rag_state_path, record.note_relpath, body).ingested
            )
        except _EFFECT_ERRORS:
            return False

    def apply(record: RelocationRecord) -> ApplyOutcome:
        plan = plan_for(record)
        if plan is None:
            return ApplyOutcome(False, "entry_absent", None, 0)
        def rehash(current: RelocationRecord) -> str:
            return relocation_action_hash(
                RelocationHashFields(
                    current.source_kind,
                    current.entry_sha256,
                    current.note_relpath,
                    current.note_plan_sha256,
                    current.destination,
                )
            )

        if record.destination == "memory":
            deps = ApplyDeps(memory_dir, lambda _relpath: native_rule(record), lambda _relpath, _body: native_rule(record) is not None, rehash, now)
        else:
            deps = ApplyDeps(
                memory_dir,
                lambda relpath: _safe_read_note(write_config.clone_dir, relpath),
                lambda relpath, body: verify_ingested(rag_state_path, relpath, body).ingested,
                rehash,
                now,
            )
        try:
            return apply_relocation(record, note_text(record) or "", deps=deps)
        except _EFFECT_ERRORS:
            return ApplyOutcome(False, "entry_absent", None, 0)

    def native_rule(record: RelocationRecord) -> bytes | None:
        """The moved rule's bytes once MEMORY.md holds it verbatim — the 'note' of a native move."""
        text = recover_entry_text(memory_dir, record)
        if text is None:
            return None
        _, memory_file = read_native(memory_dir, "memory")
        return text.encode("utf-8") if any(entry.text == text for entry in memory_file.entries) else None

    def append_rule(record: RelocationRecord) -> tuple[str, str] | None:
        text = recover_entry_text(memory_dir, record)
        if text is None:
            return None
        try:
            _ = append_entry(memory_dir, "memory", text, cap=CAPS["memory"], now=now)
        except (DeletionError, OSError):
            return None
        return f"native:{record.note_relpath}", hashlib.sha256(text.encode("utf-8")).hexdigest()

    return MoveEffects(write, verified, apply)


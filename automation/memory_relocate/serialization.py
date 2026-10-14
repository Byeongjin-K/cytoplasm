"""Stable JSON projection of relocation records (optional fields stay optional)."""
from __future__ import annotations

from .model import RelocationRecord, RelocationState

_VERSION = 1

def _serialize_record(record: RelocationRecord) -> dict[str, object]:
    optional = (
        {"approval_thread_id": record.approval_thread_id}
        if record.approval_thread_id is not None
        else {}
    )
    if record.approval_guild_id is not None:
        optional["approval_guild_id"] = record.approval_guild_id
    if record.render_version is not None:
        optional["render_version"] = record.render_version
    if record.destination is not None:
        optional["destination"] = record.destination
    if record.batch_hash is not None:
        optional["batch_hash"] = record.batch_hash
    if record.batch_index is not None:
        optional["batch_index"] = record.batch_index
    if record.choice is not None:
        optional["choice"] = record.choice.value
    return {
        **optional,
        "version": record.version,
        "source_kind": record.source_kind,
        "entry_sha256": record.entry_sha256,
        "note_relpath": record.note_relpath,
        "note_plan_sha256": record.note_plan_sha256,
        "reclaimable_chars": record.reclaimable_chars,
        "action_hash": record.action_hash,
        "status": record.status,
        "kind": record.kind,
        "surface": record.surface,
        "channel_id": record.channel_id,
        "policy_version": record.policy_version,
        "message_id": record.message_id,
        "created_at": record.created_at,
        "approved_at": record.approved_at,
        "written_at": record.written_at,
        "reconciled_at": record.reconciled_at,
        "remote_ref": record.remote_ref,
        "note_content_sha256": record.note_content_sha256,
        "rag_source_key": record.rag_source_key,
        "rag_fingerprint": record.rag_fingerprint,
        "backup_path": record.backup_path,
        "last_block_reason": record.last_block_reason,
    }


def serialize_state(s: RelocationState) -> dict[str, object]:
    return {
        "version": _VERSION,
        "relocations": {
            key: _serialize_record(record) for key, record in s.relocations.items()
        },
    }


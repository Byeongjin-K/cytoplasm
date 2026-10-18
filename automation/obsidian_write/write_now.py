"""Cardless writes for owner commands verified by the upstream YooDori gate.

The caller owns origin verification, the release switch and the daily ledger.
This module keeps the frozen body and receipt in the existing request audit store,
without creating a card or a reaction approval.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from . import gate_binding
from .config import ObsidianWriteError, load_config
from .note import NotePlan
from .note_request import NoteRequest, NoteRequestStore, body_sha256, request_key
from .writer import WriteReceipt, write_note


def write_now(plan: NotePlan, origin_channel_id: str, origin_message_id: str) -> WriteReceipt:
    """Freeze, write and audit an owner command using the shared verified writer."""
    if not origin_channel_id.strip() or not origin_message_id.strip():
        raise ObsidianWriteError("owner command requires instruction coordinates", False)
    config = load_config()
    store = NoteRequestStore()
    decision = gate_binding.evaluate(plan)
    record = NoteRequest(
        request_id=uuid4().hex[:16], key=request_key(plan.relpath.as_posix()),
        relpath=plan.relpath.as_posix(), title=plan.title,
        body_sha256=body_sha256(plan), action_hash=decision.action_hash,
        target_id=decision.target_id,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        status="writing", method="owner_command",
        origin_channel_id=origin_channel_id, origin_message_id=origin_message_id,
    )
    store.create(record, plan)
    try:
        frozen = store.frozen_plan(record)
        receipt = write_note(
            frozen, config,
            authorize_push=lambda candidate: candidate == store.frozen_plan(record),
        )
    except (ObsidianWriteError, OSError) as error:
        store.update(replace(
            record, status="failed", attempts=1, last_error=str(error)[:300],
            finished_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))
        raise
    store.update(replace(
        record, status="written", attempts=1,
        remote_ref=receipt.remote_ref, content_sha256=receipt.content_sha256,
        finished_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    ))
    return receipt

"""Production effects wiring the plaud FSM to Discord, the push gate and the vault."""

from __future__ import annotations

import hashlib
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.error import HTTPError

from automation import auto_write
from automation.interop import origin_notice
from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import ApprovalRequest, Probe
from automation.interop.approval_surface import (
    ApprovalKind,
    RequestThread,
    resolve_new_binding,
    reuse_request_thread,
)
from automation.interop.discord_transport import DiscordTransport as NoticeSender
from automation.interop.external_effect_gate import ApprovalContext
from automation.interop.origin_notice import ThreadOutcome
from automation.interop.reaction_approval import thread_candidates as shared_thread_candidates
from automation.obsidian_write import gate_binding
from automation.obsidian_write.config import ObsidianWriteError, load_config
from automation.obsidian_write.note import NotePlan
from automation.obsidian_write.writer import write_note

from .approval_gate import PlaudApprovalGate, request_approval
from .model import PlaudSyncRecord
from .reaction_transport import DiscordTransport, record_push_approval
from .render import prepare_approval_card, summary_preview
from .result_message import result_notice_message
from .store import PlaudSyncStore, load_note_body
from .watch_step import ResolveEffects

_EFFECT_ERRORS: Final = (OSError, ValueError, RuntimeError, ObsidianWriteError)

#: Result words that END a request. Anything else is an intermediate acknowledgement
#: and must leave the thread open — archiving a live request hides it from the owner's
#: active-request list (that list IS the open-request list, 소유자 결정 2026-09-01).
_TERMINAL_OUTCOMES: Final = {
    "written": ThreadOutcome.DONE,
    "ingested": ThreadOutcome.DONE,
    "done": ThreadOutcome.DONE,
    "abandoned": ThreadOutcome.CANCELLED,
    "cancelled": ThreadOutcome.CANCELLED,
    "rejected": ThreadOutcome.CANCELLED,
    "expired": ThreadOutcome.EXPIRED,
}


def note_plan_for(state_dir: Path, record: PlaudSyncRecord) -> NotePlan | None:
    try:
        body = load_note_body(state_dir, record.recording_id)
    except _EFFECT_ERRORS:
        return None
    if body is None:
        return None
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != record.body_sha256:
        return None
    return NotePlan(PurePosixPath(record.note_relpath), record.note_title, body)


def result_notice_text(record: PlaudSyncRecord, outcome: str) -> str:
    if outcome == "written":
        return f"✅ lifelog 저장 완료: `{record.note_relpath}`"
    return f"⛔ 취소됨: 녹음 `{record.recording_id}` 노트는 저장되지 않았습니다."


def record_write_failure(store: PlaudSyncStore, record: PlaudSyncRecord, error: Exception) -> None:
    """Pin why the vault write failed on the record and say so on stderr.

    A swallowed write failure left an approved record silent for the owner
    (2026-09-02: the write clone's fetch timed out every tick). The record stays
    ``approved`` so the next tick retries; the reason is what the status skill shows.
    """
    reason = f"write: {type(error).__name__}: {error}"[:200]
    print(f"plaud-sync write error: {record.recording_id} {reason}", file=sys.stderr)
    try:
        store.update(replace(record, last_block_reason=reason))
    except _EFFECT_ERRORS:
        return


def thread_candidates(
    record: PlaudSyncRecord, live: tuple[PlaudSyncRecord, ...]
) -> tuple[PlaudSyncRecord, ...]:
    """Where this request's card belongs: its live request, else the thread it opened.

    Record-shaped wrapper over the shared helper — the rule (one request keeps one
    thread even after its message goes missing) is identical for memory_relocate, so
    it lives in interop; only the rebinding of THIS record type belongs here.
    """
    return shared_thread_candidates(
        live,
        approval_thread_id=record.approval_thread_id,
        rebind=lambda thread_id: replace(record, channel_id=thread_id),
    )


def build_effects(
    *, state_path: Path, token: str, owner_id: str, now: datetime
) -> ResolveEffects:
    state_dir = state_path.parent
    store = PlaudSyncStore(state_path)
    transport = DiscordTransport(token, owner_id)
    directory = DiscordChannelDirectory(
        token, owner_id, transport.api, state_dir / "approval-directory.json"
    )
    lease = FileKeyLease(state_dir / "approval-leases")
    journal = PostingJournal(state_dir / "posting-journal")
    write_config = load_config()
    push_approval_log = state_dir / "push-approvals.jsonl"
    automatic = auto_write.enabled("plaud")

    def live_requests(key: str) -> tuple[PlaudSyncRecord, ...]:
        try:
            return tuple(
                pending
                for pending in store.all_pending()
                if pending.recording_id == key and pending.message_id
            )
        except _EFFECT_ERRORS:
            return ()

    def post(record: PlaudSyncRecord) -> tuple[str, str] | None:
        try:
            plan = note_plan_for(state_dir, record)
            if plan is None:
                return None
            preview = summary_preview(plan.body)
            live = live_requests(record.recording_id)
            card = None if live else prepare_approval_card(record, preview)
            binding = reuse_request_thread(
                ApprovalKind.OBSIDIAN_WRITE,
                thread_candidates(record, live),
                directory,
                owner_id,
            ) or resolve_new_binding(
                ApprovalKind.OBSIDIAN_WRITE,
                directory,
                owner_id,
                request=RequestThread(title=PurePosixPath(record.note_relpath).name),
            )
            bound = replace(
                record, approval_thread_id=binding.channel_id,
                approval_guild_id=binding.guild_id or record.approval_guild_id,
            )
            store.update(bound)
            verdict = request_approval(
                bound,
                preview=preview, card=card,
                store=store,
                transport=transport,
                binding=binding,
                lease=lease,
                journal=journal,
            )
        except _EFFECT_ERRORS:
            return None
        if verdict.posted is not None:
            return verdict.posted.message_id, verdict.posted.channel_id
        if verdict.live is not None:
            return verdict.live.message_id, verdict.live.channel_id
        return None

    def probe(record: PlaudSyncRecord) -> str:
        try:
            if (
                note_plan_for(state_dir, record) is None
                or record.message_id is None
                or not record.channel_id
            ):
                return "pending"
            request = ApprovalRequest(
                record.recording_id,
                record.action_hash,
                record.message_id,
                record.channel_id,
                record.created_at,
            )
            decision = PlaudApprovalGate(record, store, transport).probe(request)
        except _EFFECT_ERRORS:
            return "pending"
        match decision:
            case Probe.APPROVED:
                return "approved"
            case Probe.CANCELLED:
                return "cancelled"
            case Probe.MISSING:
                return "missing"
            case _:
                return "pending"

    def write(record: PlaudSyncRecord) -> tuple[str, str] | None:
        plan = note_plan_for(state_dir, record)
        auto = automatic and record.status == "planned" and record.message_id is None
        if plan is None or (not auto and (record.message_id is None or record.status != "approved")):
            return None
        if auto:
            try:
                receipt = write_note(
                    plan, write_config,
                    authorize_push=lambda candidate: (
                        auto_write.enabled("plaud")
                        and candidate == note_plan_for(state_dir, record)
                    ),
                )
                auto_write.record("plaud", auto_write.Entry(
                    record.recording_id, record.note_relpath, receipt.commit_sha,
                    record.recorded_at, now.isoformat(),
                ))
            except _EFFECT_ERRORS as error:
                record_write_failure(store, record, error)
                return None
            return receipt.remote_ref, receipt.content_sha256
        context = ApprovalContext(
            approval_log=push_approval_log, owner_id=owner_id, e2e_test_mode=False
        )
        try:
            decision = gate_binding.evaluate(plan, context=context)
            if not decision.allowed:
                record_push_approval(
                    push_approval_log,
                    action_hash=decision.action_hash,
                    target_id=decision.target_id,
                    owner_id=owner_id,
                    message_id=record.message_id,
                    now=now,
                )
            receipt = write_note(plan, write_config, approval_context=context)
        except _EFFECT_ERRORS as error:
            record_write_failure(store, record, error)
            return None
        return receipt.remote_ref, receipt.content_sha256

    def notify(record: PlaudSyncRecord, outcome: str) -> None:
        """Return the result to the request's OWN thread, and close it when terminal.

        Through the shared ``origin_notice`` so the rule holds identically everywhere:
        the approval surface (✅/⛔) stays approval-only and the thread is renamed +
        archived on a terminal result. Best-effort by contract — a notice never changes
        an exit code, a receipt or the store, so a total failure ends as a NOTIFY-FAIL
        marker on stderr rather than as an exception the tick dies on.
        """
        if automatic and record.message_id is None:
            return
        channel = record.approval_thread_id or record.channel_id
        if not channel:
            return
        try:
            notice_record = {
                "id": record.recording_id, "origin_channel_id": record.channel_id,
                "approval_thread_id": record.approval_thread_id or "",
                "approval_guild_id": record.approval_guild_id,
                "message_id": record.message_id,
            }
            content = result_notice_text(record, outcome)
            message = result_notice_message(record, content, outcome)
            if message is not None and getattr(origin_notice, "ACCEPTS_OWNER_MESSAGE", False):
                origin_notice.deliver(
                    api=transport.api,
                    transport_factory=lambda thread_id: NoticeSender(token, thread_id),
                    record=notice_record, thread_name=record.recording_id,
                    content=content, message=message,
                    fallback=lambda content: transport.post_message(channel, content),
                    fallback_destination=replace(
                        message.location, scope="channel", channel_id=channel, message_id=None,
                    ),
                    outcome=_TERMINAL_OUTCOMES.get(outcome),
                )
                return
            origin_notice.deliver(
                api=transport.api,
                transport_factory=lambda thread_id: NoticeSender(token, thread_id),
                record=notice_record, thread_name=record.recording_id, content=content,
                fallback=lambda content: transport.post_message(channel, content),
                outcome=_TERMINAL_OUTCOMES.get(outcome),
            )
        except (HTTPError, *_EFFECT_ERRORS) as error:
            print(
                f"NOTIFY-FAIL id={record.recording_id} err={type(error).__name__}",
                file=sys.stderr,
            )

    return ResolveEffects(post, probe, write, notify, now, auto_write=automatic)

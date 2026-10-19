"""One reaction-watcher tick for note requests: probe the bound card, write on ✅.

The only path from an approved card to the vault. A ✅ read here (owner, non-bot, on
the card whose content still carries the action hash) is transcribed once into the
gate's approval log, then ``write_note`` pushes exactly the frozen (path, title, body)
and reads the remote back. Receipts and cancellations return to the request's thread.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from automation.interop import origin_notice
from automation.interop.approval_lifecycle import ApprovalRequest, ApprovalSurfaceError, Probe
from automation.interop.discord_transport import DiscordTransport as NoticeSender
from automation.interop.effect_approval import ApprovalContext
from automation.interop.reaction_approval import record_push_approval

from . import gate_binding
from .config import ObsidianWriteConfig, ObsidianWriteError
from .note_request import NoteRequest, NoteRequestStore
from .note_request_gate import DiscordRuntime, NoteApprovalGate
from .writer import WriteReceipt, write_note

MAX_WRITE_ATTEMPTS: Final = 5
Writer = Callable[..., WriteReceipt]


@dataclass(frozen=True, slots=True)
class TickResult:
    written: tuple[str, ...] = ()
    cancelled: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


def run_tick(
    store: NoteRequestStore,
    runtime: DiscordRuntime,
    config: ObsidianWriteConfig,
    *,
    token: str,
    now: datetime,
    writer: Writer = write_note,
) -> TickResult:
    written: list[str] = []
    cancelled: list[str] = []
    failed: list[str] = []
    for record in store.all():
        if record.status == "posted":
            record = _probe(record, store, runtime, now)
            if record.status in {"cancelled", "abandoned"}:
                cancelled.append(record.request_id)
                _notify(record, runtime, token, "cancelled")
        if record.status == "approved":
            record = _write(record, store, runtime, config, now, writer)
            if record.status == "written":
                written.append(record.request_id)
                _notify(record, runtime, token, "written")
            elif record.status == "failed":
                failed.append(record.request_id)
                _notify(record, runtime, token, "failed")
    return TickResult(tuple(written), tuple(cancelled), tuple(failed))


def _probe(record: NoteRequest, store: NoteRequestStore, runtime: DiscordRuntime, now: datetime) -> NoteRequest:
    if not record.message_id or not record.channel_id:
        return record
    request = ApprovalRequest(record.key, record.action_hash, record.message_id, record.channel_id, record.created_at)
    try:
        verdict = NoteApprovalGate(record, store, runtime.transport).probe(request)
    except ApprovalSurfaceError:
        return record
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    match verdict:
        case Probe.APPROVED:
            updated = replace(record, status="approved")
        case Probe.CANCELLED:
            updated = replace(record, status="cancelled", finished_at=stamp)
        case Probe.MISSING:
            updated = replace(record, status="abandoned", finished_at=stamp, last_error="approval card is gone")
        case _:
            return record
    store.update(updated)
    return updated


def _write(
    record: NoteRequest,
    store: NoteRequestStore,
    runtime: DiscordRuntime,
    config: ObsidianWriteConfig,
    now: datetime,
    writer: Writer,
) -> NoteRequest:
    approval_log = store.root / "push-approvals.jsonl"
    context = ApprovalContext(approval_log=approval_log, owner_id=runtime.owner_id, e2e_test_mode=False)
    try:
        plan = store.frozen_plan(record)
        decision = gate_binding.evaluate(plan, context=context)
        if decision.action_hash != record.action_hash:
            raise ObsidianWriteError("note request hash no longer matches its frozen body", False)
        if not decision.allowed and record.message_id:
            record_push_approval(
                approval_log,
                action_hash=decision.action_hash,
                target_id=decision.target_id,
                owner_id=runtime.owner_id,
                message_id=record.message_id,
                now=now,
            )
        receipt = writer(plan, config, approval_context=context)
    except ObsidianWriteError as error:
        attempts = record.attempts + 1
        final = not error.retryable or attempts >= MAX_WRITE_ATTEMPTS
        updated = replace(
            record, attempts=attempts, last_error=str(error)[:300],
            status="failed" if final else "approved",
            finished_at=now.strftime("%Y-%m-%dT%H:%M:%SZ") if final else "",
        )
        store.update(updated)
        return updated
    updated = replace(
        record, status="written", attempts=record.attempts + 1, last_error="",
        remote_ref=receipt.remote_ref, content_sha256=receipt.content_sha256,
        finished_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    store.update(updated)
    return updated


def notice_text(record: NoteRequest, outcome: str) -> str:
    match outcome:
        case "written":
            return (
                f"✅ Obsidian 노트 저장 완료: {record.relpath}\n"
                f"원격 {record.remote_ref} 에서 다시 읽은 내용 sha256 {record.content_sha256} 가 일치한다."
            )
        case "cancelled" if record.status == "abandoned":
            return f"⛔ 승인 카드가 사라져 저장하지 않았다: {record.relpath} — 다시 요청해야 한다."
        case "cancelled":
            return f"⛔ Obsidian 노트 저장 취소: {record.relpath} — 볼트는 바뀌지 않았다."
        case _:
            return (
                f"❌ Obsidian 노트 저장 실패: {record.relpath} — {record.last_error} "
                f"(시도 {record.attempts}회, 볼트는 바뀌지 않았다)"
            )


def _message(record: NoteRequest, content: str, outcome: str) -> object | None:
    try:
        from automation.interop.origin_notice import approval_location
        from automation.interop.owner_message import Action, OwnerMessage, Result
    except Exception:  # noqa: BLE001 - optional envelope; the string notice still goes out
        return None
    detail = {"written": "executed", "cancelled": "cancelled"}.get(outcome)
    if detail is None:
        return None
    return OwnerMessage(
        subject_key=record.request_id, subject=record.relpath, fact=content,
        location=approval_location(_notice_record(record), search=("Discord 검색", record.request_id)),
        owner=Action(verb="none"), agent_next=None, recovery="not_applicable",
        detail=Result(outcome=detail),
    )


def _notice_record(record: NoteRequest) -> dict[str, str]:
    return {
        "id": record.request_id,
        "origin_channel_id": record.origin_channel_id,
        "origin_message_id": record.origin_message_id,
        "approval_thread_id": record.approval_thread_id,
        "approval_guild_id": record.approval_guild_id,
        "message_id": record.message_id or "",
    }


def _notify(record: NoteRequest, runtime: DiscordRuntime, token: str, outcome: str) -> None:
    channel = record.approval_thread_id or record.channel_id
    if not channel:
        return
    terminal = {"written": origin_notice.ThreadOutcome.DONE, "cancelled": origin_notice.ThreadOutcome.CANCELLED}
    content = notice_text(record, outcome)
    message = _message(record, content, outcome)
    try:
        if message is not None and getattr(origin_notice, "ACCEPTS_OWNER_MESSAGE", False):
            origin_notice.deliver(
                api=runtime.transport.api,
                transport_factory=lambda thread_id: NoticeSender(token, thread_id),
                record=_notice_record(record), thread_name=record.request_id,
                content=content, message=message,
                fallback=lambda body: runtime.transport.post_message(channel, body),
                outcome=terminal.get(outcome),
            )
            return
        origin_notice.deliver(
            api=runtime.transport.api,
            transport_factory=lambda thread_id: NoticeSender(token, thread_id),
            record=_notice_record(record), thread_name=record.request_id, content=content,
            fallback=lambda body: runtime.transport.post_message(channel, body),
            outcome=terminal.get(outcome),
        )
    except Exception as error:  # noqa: BLE001 - a notice never changes the tick or the receipt
        print(f"NOTIFY-FAIL id={record.request_id} err={type(error).__name__}", file=sys.stderr)

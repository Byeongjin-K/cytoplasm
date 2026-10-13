"""One reaction-watcher tick: probe each bound proposal card, apply on ✅, close on ⛔.

The only path from an approved card into the project data. The frozen proposal is
re-hashed against the record before it is applied, so the store receives exactly what
the owner saw. Results go back to the request's thread through ``origin_notice``.
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

from .board_store import BoardStore, utc_stamp
from .models import ProjectBoardError
from .proposal_gate import DiscordRuntime, ProposalApprovalGate
from .proposals import ProposalRecord, ProposalStore

MAX_APPLY_ATTEMPTS: Final = 3


@dataclass(frozen=True, slots=True)
class TickResult:
    applied: tuple[str, ...] = ()
    cancelled: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


def run_tick(
    proposals: ProposalStore,
    board: BoardStore,
    runtime: DiscordRuntime,
    *,
    token: str,
    now: datetime,
    on_change: Callable[[], None],
) -> TickResult:
    applied: list[str] = []
    cancelled: list[str] = []
    failed: list[str] = []
    for record in proposals.all():
        if record.status == "posted":
            record = _probe(record, proposals, runtime, now)
            if record.status in {"cancelled", "abandoned"}:
                cancelled.append(record.request_id)
                _notify(record, runtime, token, "cancelled")
        if record.status == "approved":
            record = _apply(record, proposals, board, now)
            if record.status == "applied":
                applied.append(record.request_id)
            elif record.status == "failed":
                failed.append(record.request_id)
                _notify(record, runtime, token, "failed")
    if applied:
        try:
            on_change()
        except Exception as error:  # noqa: BLE001 - a stale dashboard never undoes an applied approval
            print(f"DASHBOARD-REBUILD-FAIL err={type(error).__name__}", file=sys.stderr)
        for request_id in applied:
            record = proposals.get(request_id)
            if record is not None:
                _notify(record, runtime, token, "applied")
    return TickResult(tuple(applied), tuple(cancelled), tuple(failed))


def _probe(record: ProposalRecord, store: ProposalStore, runtime: DiscordRuntime, now: datetime) -> ProposalRecord:
    if not record.message_id or not record.channel_id:
        return record
    request = ApprovalRequest(record.key, record.action_hash, record.message_id, record.channel_id, record.created_at)
    try:
        verdict = ProposalApprovalGate(record, store, runtime.transport).probe(request)
    except ApprovalSurfaceError:
        return record
    match verdict:
        case Probe.APPROVED:
            updated = replace(record, status="approved")
        case Probe.CANCELLED:
            updated = replace(record, status="cancelled", finished_at=utc_stamp(now))
        case Probe.MISSING:
            updated = replace(record, status="abandoned", finished_at=utc_stamp(now), last_error="approval card is gone")
        case _:
            return record
    store.update(updated)
    return updated


def _apply(record: ProposalRecord, store: ProposalStore, board: BoardStore, now: datetime) -> ProposalRecord:
    try:
        proposal = store.frozen(record)
        added = board.apply_proposal(record.request_id, proposal, message_id=record.message_id or "", now=now)
    except (ProjectBoardError, OSError) as error:
        attempts = record.apply_attempts + 1
        final = attempts >= MAX_APPLY_ATTEMPTS
        updated = replace(
            record,
            status="failed" if final else "approved",
            apply_attempts=attempts,
            last_error=str(error)[:200],
            finished_at=utc_stamp(now) if final else "",
        )
        store.update(updated)
        return updated
    updated = replace(
        record, status="applied", applied_todos=added or record.todo_count, last_error="", finished_at=utc_stamp(now)
    )
    store.update(updated)
    return updated


def notice_text(record: ProposalRecord, outcome: str) -> str:
    match outcome:
        case "applied":
            return (
                f"✅ 프로젝트 데이터 반영: {record.project} · 할 일 {record.applied_todos}건 "
                f"(제안 {record.request_id}). 대시보드를 다시 만들었다."
            )
        case "cancelled" if record.status == "abandoned":
            return f"⛔ 승인 카드가 사라져 반영하지 않았다: {record.project} (제안 {record.request_id})"
        case "cancelled":
            return f"⛔ 프로젝트 제안 취소: {record.project} (제안 {record.request_id}) — 데이터는 바뀌지 않았다."
        case _:
            return (
                f"❌ 프로젝트 데이터 반영 실패: {record.project} (제안 {record.request_id}) — "
                f"{record.last_error} (데이터는 바뀌지 않았다)"
            )


def _notice_record(record: ProposalRecord) -> dict[str, str]:
    return {
        "id": record.request_id,
        "origin_channel_id": record.origin_channel_id,
        "origin_message_id": record.origin_message_id,
        "approval_thread_id": record.approval_thread_id,
        "approval_guild_id": record.approval_guild_id,
        "message_id": record.message_id or "",
    }


def _message(record: ProposalRecord, content: str, outcome: str) -> object | None:
    try:
        from automation.interop.origin_notice import approval_location
        from automation.interop.owner_message import Action, OwnerMessage, Result
    except Exception:  # noqa: BLE001 - optional envelope; the string notice still goes out
        return None
    detail = {"applied": "executed", "cancelled": "cancelled"}.get(outcome)
    if detail is None:
        return None
    return OwnerMessage(
        subject_key=record.request_id, subject=f"프로젝트 제안 · {record.project}", fact=content,
        location=approval_location(_notice_record(record), search=("Discord 검색", record.request_id)),
        owner=Action(verb="none"), agent_next=None, recovery="not_applicable",
        detail=Result(outcome=detail),
    )


def _notify(record: ProposalRecord, runtime: DiscordRuntime, token: str, outcome: str) -> None:
    channel = record.approval_thread_id or record.channel_id
    if not channel:
        return
    if not token:
        print(f"NOTIFY-SKIP id={record.request_id} reason=no-token", file=sys.stderr)
        return
    terminal = {"applied": origin_notice.ThreadOutcome.DONE, "cancelled": origin_notice.ThreadOutcome.CANCELLED}
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
    except Exception as error:  # noqa: BLE001 - a notice never changes the tick or the store
        print(f"NOTIFY-FAIL id={record.request_id} err={type(error).__name__}", file=sys.stderr)

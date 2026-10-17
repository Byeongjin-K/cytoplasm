"""One reaction-watcher tick for effect requests: probe the bound card, execute on ✅.

The only path from an approved generic card to the outside world. A command is claimed
(``running``) before it starts, so a tick that dies mid-run leaves a record the next tick
reports as unknown instead of running it a second time. Receipts and cancellations go
back to the request's own thread through ``origin_notice``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from automation.interop import origin_notice
from automation.interop.approval_lifecycle import ApprovalRequest, ApprovalSurfaceError, Probe
from automation.interop.discord_transport import DiscordTransport as NoticeSender

from .execute import Publisher, Runner, execute
from .model import COMMAND, EffectRequest, EffectRequestError, EffectRequestStore
from .gate import DiscordRuntime, EffectApprovalGate
from .render import drive_location

MAX_ATTEMPTS: Final = 5


@dataclass(frozen=True, slots=True)
class TickResult:
    done: tuple[str, ...] = ()
    cancelled: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


def _stamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_tick(
    store: EffectRequestStore,
    runtime: DiscordRuntime,
    *,
    token: str,
    now: datetime,
    publisher: Publisher,
    runner: Runner | None = None,
) -> TickResult:
    done: list[str] = []
    cancelled: list[str] = []
    failed: list[str] = []
    for record in store.all():
        if record.status == "running":
            record = _finish(store, record, now, error="실행 도중 멈춰 결과를 알 수 없다 — 다시 실행하지 않는다")
            failed.append(record.request_id)
            _notify(record, runtime, token, "failed")
            continue
        if record.status == "posted":
            record = _probe(record, store, runtime, now)
            if record.status in {"cancelled", "abandoned"}:
                cancelled.append(record.request_id)
                _notify(record, runtime, token, "cancelled")
        if record.status == "approved":
            record = _execute(record, store, now, publisher, runner)
            if record.status == "done":
                done.append(record.request_id)
                _notify(record, runtime, token, "done")
            elif record.status == "failed":
                failed.append(record.request_id)
                _notify(record, runtime, token, "failed")
    return TickResult(tuple(done), tuple(cancelled), tuple(failed))


def _probe(record: EffectRequest, store: EffectRequestStore, runtime: DiscordRuntime, now: datetime) -> EffectRequest:
    if not record.message_id or not record.channel_id:
        return record
    request = ApprovalRequest(record.key, record.action_hash, record.message_id, record.channel_id, record.created_at)
    try:
        verdict = EffectApprovalGate(record, store, runtime.transport).probe(request)
    except ApprovalSurfaceError:
        return record
    match verdict:
        case Probe.APPROVED:
            updated = replace(record, status="approved")
        case Probe.CANCELLED:
            updated = replace(record, status="cancelled", finished_at=_stamp(now))
        case Probe.MISSING:
            updated = replace(record, status="abandoned", finished_at=_stamp(now), last_error="승인 카드가 사라졌다")
        case _:
            return record
    store.update(updated)
    return updated


def _finish(store: EffectRequestStore, record: EffectRequest, now: datetime, *, error: str) -> EffectRequest:
    updated = replace(record, status="failed", last_error=error[:300], finished_at=_stamp(now))
    store.update(updated)
    return updated


def _execute(
    record: EffectRequest,
    store: EffectRequestStore,
    now: datetime,
    publisher: Publisher,
    runner: Runner | None,
) -> EffectRequest:
    attempts = record.attempts + 1
    if record.effect == COMMAND:
        record = replace(record, status="running", attempts=attempts)
        store.update(record)
    try:
        receipt = execute(record, publisher=publisher, **({"runner": runner} if runner else {}))
    except EffectRequestError as error:
        final = record.effect == COMMAND or not error.retryable or attempts >= MAX_ATTEMPTS
        updated = replace(
            record, attempts=attempts, last_error=str(error)[:300],
            status="failed" if final else "approved", finished_at=_stamp(now) if final else "",
        )
        store.update(updated)
        return updated
    updated = replace(record, status="done", attempts=attempts, last_error="", receipt=receipt, finished_at=_stamp(now))
    store.update(updated)
    return updated


def notice_text(record: EffectRequest, outcome: str) -> str:
    what = record.summary
    receipt = record.receipt or {}
    match outcome:
        case "done" if record.effect == COMMAND:
            return f"✅ 외부 작업 실행 완료: {what}\n종료 코드 0 · 출력 끝부분: {receipt.get('tail') or '(없음)'}"
        case "done":
            links = ", ".join(str(link) for link in receipt.get("links", ())) or "(링크 없음)"
            return (
                f"✅ Drive 저장 완료(비공개): {drive_location(record.payload)}\n"
                f"다시 내려받은 sha256 이 승인한 파일과 같다. 링크: {links}"
            )
        case "cancelled" if record.status == "abandoned":
            return f"⛔ 승인 카드가 사라져 실행하지 않았다: {what} — 다시 요청해야 한다."
        case "cancelled":
            return f"⛔ 취소됨: {what} — 아무것도 실행하지 않았다."
        case _:
            return f"❌ 실패: {what} — {record.last_error} (시도 {record.attempts}회)"


def _notice_record(record: EffectRequest) -> dict[str, str]:
    return {
        "id": record.request_id,
        "origin_channel_id": record.origin_channel_id,
        "origin_message_id": record.origin_message_id,
        "approval_thread_id": record.approval_thread_id,
        "approval_guild_id": record.approval_guild_id,
        "message_id": record.message_id or "",
    }


def _message(record: EffectRequest, content: str, outcome: str) -> object | None:
    try:
        from automation.interop.origin_notice import approval_location
        from automation.interop.owner_message import Action, OwnerMessage, Result
    except Exception:  # noqa: BLE001 - optional envelope; the string notice still goes out
        return None
    detail = {"done": "executed", "cancelled": "cancelled"}.get(outcome)
    if detail is None:
        return None
    return OwnerMessage(
        subject_key=record.request_id, subject=record.summary, fact=content,
        location=approval_location(_notice_record(record), search=("Discord 검색", record.request_id)),
        owner=Action(verb="none"), agent_next=None, recovery="not_applicable",
        detail=Result(outcome=detail),
    )


def _notify(record: EffectRequest, runtime: DiscordRuntime, token: str, outcome: str) -> None:
    channel = record.approval_thread_id or record.channel_id
    if not channel:
        return
    terminal = {"done": origin_notice.ThreadOutcome.DONE, "cancelled": origin_notice.ThreadOutcome.CANCELLED}
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

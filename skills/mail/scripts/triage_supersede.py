"""Close an undecided mail card without the owner: a newer draft, or its requester's withdrawal.

Owner 2026-10-19 00:00 KST: revising one mail three times in one thread must not leave three
live cards. Only compose drafts written from that same thread qualify (a digest thread holds
cards of different mails). A card the owner already decided is left to the watcher.

Owner Q367 (2026-10-26 18:59 KST, "유돌이 자기 카드를 취소만 할 수 있게"): ``withdraw`` lets the
requester take back its own pending card under the same lease and probe. It only ever makes a
card terminal; whose card it is, the YooDori gate decides before calling it.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from importlib import import_module

import triage_gate

REASON = "superseded-by-newer-draft"
NOTICE = "🔁 새 판으로 대체됨 — 이 카드는 실행되지 않습니다. 아래 새 카드에서 승인해 주세요."
WITHDRAWN = "withdrawn-by-requester"


def _same_request(old: dict, new: dict) -> bool:
    # A thread's first draft comes from the parent-channel message that opened the thread, and a
    # Discord thread's id is that message's id (2026-10-19: af9375 was never closed for this).
    thread = new.get("approval_thread_id")
    return (
        old.get("id") != new.get("id") and old.get("kind") == "compose" and old.get("status") == "pending"
        and bool(thread) and old.get("approval_thread_id") == thread
        and thread in (old.get("origin_channel_id"), old.get("origin_message_id"))
    )


def supersede_older(draft: dict) -> list[str]:
    thread = draft.get("approval_thread_id")
    if draft.get("kind") != "compose" or not thread or draft.get("origin_channel_id") != thread:
        return []
    closed = []
    for record in triage_gate.list_drafts():
        if not _same_request(record, draft):
            continue
        try:
            if _close(record):
                closed.append(str(record["id"]))
        except Exception as error:  # noqa: BLE001 — the new card is already live; the old one stays as it was
            print(f"MAIL-SUPERSEDE-FAIL draft={record.get('id')} err={type(error).__name__}", file=sys.stderr)
    return closed


def _close(record: dict) -> bool:
    """Expire only the old card, never the thread holding the newer pending card."""
    approval = import_module("triage_approval")
    gate_module = import_module("triage_approval_gate")
    key = approval.approval_key(record)
    with approval.confirm_lease().hold(key) as owned:
        if not owned:
            return False
        message_id = approval._bound_message_id(record)
        if not message_id:
            triage_gate.expire_draft(str(record["id"]), "", REASON)
            return True
        request = approval.request_of(record)
        state = approval.lifecycle().Probe
        probe = gate_module.MailApprovalGate(record).probe(request)
        if probe in (state.APPROVED, state.CANCELLED):
            return False
        triage_gate.expire_draft(str(record["id"]), message_id, REASON)
        if probe is state.BOUND_PENDING:
            _mark(request.channel_id, message_id)
        return True


def withdraw(draft_id: str, reason: str, by: str) -> dict:
    """Expire one pending card its requester no longer wants; refuse anything the owner decided."""
    path = triage_gate._draft_path(draft_id)
    if path is None:
        raise triage_gate.GateError(f"카드 없음: {draft_id}", 3)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "pending":
        raise triage_gate.GateError(f"카드 {draft_id} 상태={record.get('status')} — 대기 중인 카드만 거둘 수 있습니다", 1)
    approval = import_module("triage_approval")
    gate_module = import_module("triage_approval_gate")
    with approval.confirm_lease().hold(approval.approval_key(record)) as owned:
        if not owned:
            raise triage_gate.GateError(f"카드 {draft_id} 는 지금 처리(발송) 중입니다", 1)
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") != "pending":
            raise triage_gate.GateError(f"카드 {draft_id} 상태={record.get('status')}", 1)
        message_id = approval._bound_message_id(record)
        state = approval.lifecycle().Probe
        probe = None
        if message_id:
            probe = gate_module.MailApprovalGate(record).probe(approval.request_of(record))
            if probe in (state.APPROVED, state.CANCELLED, state.UNVERIFIABLE):
                raise triage_gate.GateError(
                    f"카드 {draft_id} 는 형이 이미 결정했거나 확인할 수 없어 거두지 않습니다", 1)
        stamp = {"withdrawn_by": by, "withdraw_reason": reason,
                 "withdrawn_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        triage_gate.expire_draft(draft_id, message_id, WITHDRAWN, stamp)
        if probe is state.BOUND_PENDING:
            _mark(record["channel_id"], message_id,
                  f"⛔ 발송 취소 ({by} 거둠) — 이 카드의 메일은 나가지 않습니다. 사유: {reason}")
    _notify_withdrawn({**record, **stamp}, by, reason)
    return {**record, **stamp, "status": "expired", "expired_reason": WITHDRAWN}


def _notify_withdrawn(record: dict, by: str, reason: str) -> None:
    """The owner's ⛔ notice text, posted without closing or renaming the reused thread."""
    confirm = import_module("triage_confirm")
    try:
        confirm.notify_result(
            record,
            f"⛔ 발송 취소: {record['subject']} → {record['to']} (draft {record['id']})\n"
            f"요청한 {by} 거둠(사유: {reason}). 메일은 발송되지 않았습니다.",
        )
    except Exception as error:  # noqa: BLE001 — the withdrawal is already committed
        print(f"NOTIFY-FAIL draft={record.get('id')} err={type(error).__name__}", file=sys.stderr)


def _mark(channel_id: str, message_id: str, notice: str = NOTICE) -> None:
    confirm = import_module("triage_confirm")
    try:
        confirm._api("PATCH", f"/channels/{channel_id}/messages/{message_id}", {"content": notice})
    except Exception as error:  # noqa: BLE001 — the record is already terminal; the mark is cosmetic
        print(f"MAIL-SUPERSEDE-MARK-FAIL message={message_id} err={type(error).__name__}", file=sys.stderr)

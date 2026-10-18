"""A newer draft of the same owner request replaces the older undecided cards in its thread.

Owner 2026-10-19 00:00 KST: revising one mail three times in one thread must not leave three
live cards. Only compose drafts written from that same thread qualify (a digest thread holds
cards of different mails). A card the owner already decided is left to the watcher.
"""
from __future__ import annotations

import sys
from importlib import import_module

import triage_gate

REASON = "superseded-by-newer-draft"
NOTICE = "🔁 새 판으로 대체됨 — 이 카드는 실행되지 않습니다. 아래 새 카드에서 승인해 주세요."


def _same_request(old: dict, new: dict) -> bool:
    thread = new.get("approval_thread_id")
    return (
        old.get("id") != new.get("id") and old.get("kind") == "compose" and old.get("status") == "pending"
        and bool(thread) and old.get("approval_thread_id") == thread == old.get("origin_channel_id")
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


def _mark(channel_id: str, message_id: str) -> None:
    confirm = import_module("triage_confirm")
    try:
        confirm._api("PATCH", f"/channels/{channel_id}/messages/{message_id}", {"content": NOTICE})
    except Exception as error:  # noqa: BLE001 — the record is already terminal; the mark is cosmetic
        print(f"MAIL-SUPERSEDE-MARK-FAIL message={message_id} err={type(error).__name__}", file=sys.stderr)

"""The morning digest as one standard approval thread (owner Q187, 2026-10-16).

Mail owns the digest, so it opens the day's thread and decides when it is finished; the
shared state and the thread marks live in ``automation.interop.digest_thread``. A reply
draft asked for on a digest card (✍️ or a Discord reply) records that card as its origin,
which is how the draft's approval card finds this thread and its mail card.
"""
from __future__ import annotations

import sys
from datetime import datetime
from types import ModuleType

import triage_approval
import triage_binding
import triage_confirm
import triage_gate
import triage_transport

LABEL = "기관메일"
ICON = "📬"


def _shared() -> ModuleType:
    return triage_approval._repo_module("digest_thread")


def open_for(kst_now: datetime, count: int) -> object:
    surface = triage_approval._repo_module("approval_surface")
    shared = _shared()
    day = kst_now.date().isoformat()
    title = f"다이제스트 {kst_now:%m-%d %H:%M} · {count}건"

    def opener() -> object:
        try:
            request = surface.RequestThread(title=title, label=LABEL, icon=ICON)
        except TypeError:  # an interop runtime older than the label/icon fields
            request = surface.RequestThread(title=f"{LABEL} {title}")
        kind = surface.ApprovalKind.MAIL_REPLY
        binding = surface.resolve_new_binding(
            kind, triage_binding.approval_directory(), triage_confirm.owner_id(), request=request,
        )
        return shared.DigestThread(
            day=day, channel_id=str(binding.channel_id), surface=str(binding.surface),
            policy_version=int(binding.policy_version), guild_id=binding.guild_id,
            name=surface.request_thread_name(kind, request),
        )

    thread = shared.open_thread(day, opener)
    settle()
    return thread


def _thread(channel_id: str) -> object | None:
    try:
        return _shared().find(channel_id)
    except triage_gate.GateError:
        return None


def digest_binding(surface: ModuleType, kind: object, directory: object, draft: dict) -> object | None:
    """The digest thread's binding for a reply draft asked for on one of its cards, else None.

    A thread that no longer validates falls back to the draft's own request thread: the
    card must still reach the owner, and a standard thread is always a valid surface.
    """
    if draft.get("kind") not in (None, "reply"):
        return None
    thread = _thread(str(draft.get("origin_channel_id") or ""))
    if thread is None:
        return None
    try:
        return _shared().binding(surface, kind, thread, directory, triage_confirm.owner_id())
    except Exception as error:  # noqa: BLE001 — fall back to a request thread of its own
        print(f"DIGEST-THREAD-BINDING-FAIL draft={draft.get('id')} err={type(error).__name__}", file=sys.stderr)
        return None


def card_for(draft: dict, channel_id: str) -> str:
    """The mail card a digest draft's approval card replies under, when posted in its thread."""
    origin = str(draft.get("origin_channel_id") or "")
    if origin != channel_id or _thread(channel_id) is None:
        return ""
    return str(draft.get("origin_message_id") or "")


def is_digest_draft(draft: dict) -> bool:
    """Whether this draft's approval card sits in a digest thread (results must not close it).

    Decided by structure, not by the state file: only a draft asked inside a thread has
    that thread as both its origin and its approval thread (a new request thread hangs on
    a channel message instead), so an unreadable state file can never let a result close
    a thread other cards still wait in.
    """
    thread = str(draft.get("approval_thread_id") or "")
    return bool(thread) and thread == str(draft.get("origin_channel_id") or "")


def deliver_reminder(channel_id: str, content: str) -> None:
    """Mail's reminder delivery: inside a digest thread, join a fresh reminder message (Q187)."""
    try:
        shared = _shared()
    except triage_gate.GateError:
        triage_confirm.post_approval_request(content, channel_id)
        return
    shared.post_reminder(
        channel_id, content,
        post=lambda channel, body: triage_confirm.post_approval_request(body, channel),
        edit=triage_confirm.edit_message,
    )


def settle() -> None:
    """Mark recent digest threads by their undecided mail and calendar cards (never raises)."""
    try:
        shared = _shared()
        threads = shared.recent()
        if not threads:
            return
        counts = {thread.channel_id: 0 for thread in threads}
        try:
            drafts = triage_gate.list_drafts()
        except Exception:  # noqa: BLE001 — unreadable mail cards are unknown, never zero
            shared.settle({}, triage_confirm._api)
            raise
        for draft in drafts:
            channel = str(draft.get("channel_id") or "")
            if draft.get("status") == "pending" and channel in counts:
                counts[channel] += 1
        calendar = triage_transport._calendar_digest_pending(list(counts))
        if calendar is None:  # unknown calendar cards: closing on a guess could hide one
            shared.settle({}, triage_confirm._api)
            return
        for channel, pending in calendar.items():
            if channel in counts:
                counts[channel] += pending
        shared.settle(counts, triage_confirm._api)
    except Exception as error:  # noqa: BLE001 — a thread mark never breaks the watch tick
        print(f"DIGEST-THREAD-SETTLE-FAIL err={type(error).__name__}", file=sys.stderr)

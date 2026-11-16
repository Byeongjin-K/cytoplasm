"""The day's digests as one standard approval thread (owner Q187, 2026-10-16).

The digest runs at 09:00, 13:00 and 19:00 KST (owner 2026-10-30); every run of a KST day
posts into that day's one thread, so the thread name carries the day only and each run's
first message carries its own time and count.

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
import triage_core
import triage_gate
import triage_store
import triage_transport

LABEL = "기관메일"
ICON = "📬"
#: A mail thread's name word; ``origin_notice.thread_icon`` gives names starting with it ✉️.
MAIL_THREAD_WORD = "메일"
_MAIL_THREAD_LOCK = "digest-mail-thread.lock"
#: Prefix of the file holding a just-opened mail thread until the store has it.
_MAIL_THREAD_OPENED = "digest-mail-thread-opened"
_THREAD_TYPES = (10, 11, 12)
_SUBJECT_LIMIT = 60


def _shared() -> ModuleType:
    return triage_approval._repo_module("digest_thread")


def open_for(kst_now: datetime) -> object:
    surface = triage_approval._repo_module("approval_surface")
    shared = _shared()
    day = kst_now.date().isoformat()
    title = f"다이제스트 {kst_now:%m-%d}"

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


def opened_before(kst_now: datetime) -> str:
    """The day's thread id when it was open before this run, else '' (an unreadable state says no)."""
    try:
        thread = _shared().load(kst_now.date().isoformat())
    except Exception as error:  # noqa: BLE001 — unknown: the run posts no added line
        print(f"DIGEST-THREAD-FAIL stage=load err={type(error).__name__}", file=sys.stderr)
        return ""
    return str(thread.channel_id) if thread is not None else ""


def added_line(channel_id: str, count: int) -> str:
    """The one channel line saying a later run added ``count`` cards to the day's thread (Q40)."""
    thread = _thread(channel_id)
    guild = str(getattr(thread, "guild_id", "") or "")
    try:
        owner_message = triage_approval._repo_module("owner_message")
        url = owner_message.discord_link(
            space="guild" if guild else "unknown", channel_id=channel_id, guild_id=guild or None,
        ).url
    except triage_gate.GateError:
        url = None
    where = (owner_message.masked_discord_link(url, "스레드") if url
             else f"스레드 「{getattr(thread, 'name', '') or ICON + ' ' + LABEL}」")
    return f"{ICON} 다이제스트에 {count}건 추가됨 ({where})"


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


def mail_thread(uid: str, subject: str, card_channel_id: str) -> dict[str, str]:
    """This mail's own thread for its digest 📄/✍️, opened once and then reused (owner 2026-11-16).

    Owner: "다이제스트에서 전문 확인 및 초안 작성 누르면 별도 스레드로 진행되게 해". Discord does not
    nest threads and the card sits inside the day's digest thread, so the mail's thread
    opens in that thread's parent channel (the approval channel) through the shared
    ``origin_notice.resolve_thread_id``; its full text, draft questions, preview and
    approval card all go there. The thread is keyed by the mail (uid), so pressing the
    same mail again, on either emoji or on a later digest's card, never opens a second
    one. A lock serialises the gateway press and the watch tick racing on one mail.

    The new thread's id is written to a small file in the gate directory before the store
    write, so a store failure right after Discord opened the thread is resumed by the next
    press or tick (it adopts that thread) instead of opening a second one.
    """
    import fcntl
    import hashlib
    import json

    db = triage_gate.db_path()
    found = triage_store.digest_mail_thread(db, uid)
    if found is not None:
        return found
    lock_dir = triage_gate.gate_dir()
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (lock_dir / _MAIL_THREAD_LOCK).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        found = triage_store.digest_mail_thread(db, uid)
        if found is not None:
            return found
        opened = lock_dir / f"{_MAIL_THREAD_OPENED}-{hashlib.sha256(uid.encode()).hexdigest()[:24]}.json"
        if opened.exists():  # Discord opened it but the store write did not land last time
            thread = json.loads(opened.read_text(encoding="utf-8"))
            triage_store.save_digest_mail_thread(db, uid, thread, triage_core.utc_now())
            opened.unlink(missing_ok=True)
            return triage_store.digest_mail_thread(db, uid) or thread
        channel = triage_confirm.fetch_channel(card_channel_id)
        is_thread = isinstance(channel, dict) and channel.get("type") in _THREAD_TYPES
        parent = str(channel.get("parent_id") or "") if is_thread else ""
        origin_notice = triage_confirm._origin_notice()  # noqa: SLF001 — interop runtime import
        label = f"{MAIL_THREAD_WORD} · {' '.join(subject.split())[:_SUBJECT_LIMIT] or '제목 없음'}"
        thread_id = origin_notice.resolve_thread_id(
            triage_confirm._api,  # noqa: SLF001 — shared rate-limited Discord client
            origin_notice.OriginRef(channel_id=parent or card_channel_id), label,
        )
        guild = str(channel.get("guild_id") or "") if isinstance(channel, dict) else ""
        thread = {"thread_id": str(thread_id), "guild_id": guild}
        partial = opened.with_suffix(".tmp")
        partial.write_text(json.dumps(thread), encoding="utf-8")
        partial.replace(opened)
        triage_store.save_digest_mail_thread(db, uid, thread, triage_core.utc_now())
        opened.unlink(missing_ok=True)
        return triage_store.digest_mail_thread(db, uid) or thread


def mail_binding(surface: ModuleType, kind: object, directory: object, draft: dict) -> object | None:
    """The mail's own digest thread as the binding for its reply card, else None.

    A reply the owner asked for under a digest card before any 📄/✍️ opens that mail's
    thread here (with the card's link line), so its card never lands in the digest and
    never has to move later. A thread that no longer validates, or cannot be opened, falls
    back to the usual path (digest or request thread): the card must still reach the owner.
    """
    if draft.get("kind") not in (None, "reply"):
        return None
    uid = str(draft.get("uid") or "")
    thread = triage_store.digest_mail_thread(triage_gate.db_path(), uid)
    origin = str(draft.get("origin_channel_id") or "")
    card = str(draft.get("origin_message_id") or "")
    try:
        if thread is None and uid and card and origin and _thread(origin) is not None:
            import digest_reaction_actions

            subject = str(draft.get("mail_subject") or "")
            thread = digest_reaction_actions._mail_thread(  # noqa: SLF001 — the shared open + link step
                {"uid": uid, "channel_id": origin, "message_id": card, "subject": subject}, subject)
        if thread is None:
            return None
        return surface.validate_stored_binding(  # type: ignore[attr-defined]
            surface.ApprovalBinding(  # type: ignore[attr-defined]
                kind, surface.ApprovalSurface.AGENT_CHAT_THREAD,  # type: ignore[attr-defined]
                thread["thread_id"], surface.POLICY_VERSION,  # type: ignore[attr-defined]
                thread["guild_id"] or None,
            ),
            directory, triage_confirm.owner_id(),
        )
    except Exception as error:  # noqa: BLE001 — fall back to the usual thread
        print(f"DIGEST-MAIL-THREAD-BINDING-FAIL draft={draft.get('id')} err={type(error).__name__}",
              file=sys.stderr)
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
    if not thread or thread != str(draft.get("origin_channel_id") or ""):
        return False
    try:  # a mail's own thread closes with its result like any request thread
        return not triage_store.is_digest_mail_thread(triage_gate.db_path(), thread)
    except Exception:  # noqa: BLE001 — unknown: never close a thread other cards may wait in
        return True


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

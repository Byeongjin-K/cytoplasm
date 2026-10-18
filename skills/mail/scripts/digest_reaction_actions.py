"""Digest card reactions: ✍️ drafts that mail's reply, 📄 shows its full text (2026-10-16).

Owner request (voice, 10/16 08:55 KST): "기관메일 다이제스트에서 이모지를 내가 클릭하면
회신 초안을 자동으로 써주고 혹은 전문을 자동으로 보여주는 이모지를 넣어주고".

The digest already posts one Discord message per mail inside the digest thread, so
each card is its own reaction target — no item numbers to pick. The bot pre-attaches
both emoji when the card is posted (``attach``). The owner's press reaches us two ways,
both reading reactions only — never message text — and acting on the owner's (non-bot)
ones:

- at once: the gateway's interop plugin hears ``on_raw_reaction_add`` on its existing
  Discord connection (``automation/interop/digest_reaction_listener.py``) and runs
  ``triage_cli digest-react --message-id`` → ``run_card`` for that one card (2026-10-17,
  owner: "이모지 달면 반응이 너무 느려 즉각적으로 반응할 수 있겠죠");
- as the safety net: the ``mail-triage-watch`` tick (``triage_cli watch``, every 2 min)
  calls ``run_tick`` over every recent card, so a press the gateway missed (restart,
  disconnect) is still acted on.

What each emoji does:

- 📄 posts the mail's full text (``mail_body_view.render(full=True)``, already split
  under Discord's limit) and its attachment names into the card's thread.
- ✍️ runs the existing owner-instruction draft path (``triage_cli.cmd_draft``), which
  writes the draft and posts the standard mail approval card; the thread gets a
  preview and the card link. Nothing is sent here — sending stays the owner's ✅ on
  that approval card, read by the same watch tick's approval loop. Writing takes a
  model call, so a one-line "writing…" reply goes up first, on the first attempt only.

Each (card, action) runs at most once: a row in ``digest_actions`` is claimed before
the work and marked done after it (state after success). A failure before anything
reached the thread is retried on later ticks up to ``MAX_ATTEMPTS``; a body post that
failed after some parts were already posted is not retried, so the owner never sees
the same text twice. The claim is one SQLite ``BEGIN IMMEDIATE``, so the event path and
the tick racing on the same press still run it once. When the last allowed attempt
fails, the thread is told so instead of going quiet.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.error import HTTPError

import mail_body_view
import mail_contacts
import triage_confirm
import triage_core
import triage_gate
import triage_store
import triage_transport

DRAFT_EMOJI: Final = "\u270d\ufe0f"  # ✍️
BODY_EMOJI: Final = "\U0001f4c4"  # 📄
#: Card order: the reading action first, then the writing one.
EMOJIS: Final = (BODY_EMOJI, DRAFT_EMOJI)
ACTION_BY_EMOJI: Final = {BODY_EMOJI: "body", DRAFT_EMOJI: "draft"}
#: Cards from digests sent within this window are watched (older ones are left alone).
WINDOW: Final = timedelta(days=3)
MAX_ATTEMPTS: Final = 3
_PART_LIMIT: Final = 1900
_PREVIEW_LIMIT: Final = 1200
#: The draft path needs an instruction; ✍️ carries none, so the draft stays factual.
DRAFT_INSTRUCTION: Final = (
    "다이제스트에서 ✍️ 로 요청한 회신 초안입니다. 원문 내용에 맞춰 정중하게 쓰되, "
    "사실·일정·수치는 원문에 있는 것만 쓰고 소유자 판단이 필요한 부분은 [확인 필요]로 남겨 주세요."
)
_URL: Final = re.compile(r"\burl=(\S+)")
DRAFT_ACK: Final = (
    "✍️ 회신 초안을 쓰고 있습니다 — 1분쯤 걸려요. 다 쓰면 이 카드 아래에 미리보기와 승인 카드 링크를 올립니다."
)
_ACTION_LABEL: Final = {"body": "📄 전문", "draft": "✍️ 회신 초안"}
#: The full-text thread's name word; ``origin_notice`` gives names starting with it 📄.
BODY_THREAD_LABEL: Final = "전문"
_SUBJECT_LIMIT: Final = 60
_THREAD_TYPES: Final = (10, 11, 12)


def attach(channel_id: str, message_id: str) -> None:
    """Pre-attach both emoji to one card (best-effort — a card without them still reads)."""
    for emoji in EMOJIS:
        triage_confirm.add_reaction(message_id, emoji, channel_id)


def _same_emoji(left: str, right: str) -> bool:
    """Discord may echo ✍️ with or without the U+FE0F variation selector."""
    return left.replace("\ufe0f", "") == right.replace("\ufe0f", "")


def _owner_reactions(card: dict, owner: str) -> list[str]:
    """Actions the owner (non-bot) has reacted with on this card, in ``EMOJIS`` order.

    One message read first: a reaction whose count is only the bot's own cannot hold
    the owner's, so the per-emoji user list is read only when someone else reacted.
    """
    try:
        message = triage_confirm._api(  # noqa: SLF001 — shared rate-limited Discord client
            "GET", f"/channels/{card['channel_id']}/messages/{card['message_id']}",
        )
    except HTTPError as error:
        if error.code == 404:  # the card was deleted — nothing to act on
            return []
        raise
    reactions = message.get("reactions") if isinstance(message, dict) else None
    actions: list[str] = []
    for emoji in EMOJIS:
        for reaction in reactions or []:
            name = str((reaction.get("emoji") or {}).get("name") or "")
            if not _same_emoji(name, emoji):
                continue
            others = int(reaction.get("count") or 0) - (1 if reaction.get("me") else 0)
            if others <= 0:
                break
            users = triage_confirm._reaction_users(  # noqa: SLF001
                str(card["channel_id"]), str(card["message_id"]), name,
            )
            if triage_confirm._owner_reacted(users, owner):  # noqa: SLF001
                actions.append(ACTION_BY_EMOJI[emoji])
            break
    return actions


def _inert(text: str) -> str:
    """Mail text must never ping: ``@everyone``/``<@id>`` lose their ``@``."""
    return text.replace("@", "@\u200b")


def attachment_names(mail: dict) -> list[str]:
    """File names listed in the mailon front matter (``attachments:`` YAML list)."""
    raw = str(mail.get("body") or "").replace("\r\n", "\n")
    block = mail_body_view._ATTACHMENTS.search(raw)  # noqa: SLF001 — one front-matter grammar
    if not block:
        return []
    names = []
    for line in block[1].splitlines():
        value = line.strip().removeprefix("-").strip()
        if value:
            names.append(mail_contacts.yaml_scalar(value))
    return names


def body_parts(mail: dict, subject: str) -> list[str]:
    """The 📄 reply: full text (quoted history kept) then attachment names, each ≤2000."""
    parts = [_inert(part) for part in mail_body_view.render(mail, full=True)]
    parts[0] = f"📄 **전문** · {_inert(subject)}\n{parts[0]}"
    names = attachment_names(mail)
    if names:
        lines = [f"📎 첨부 {len(names)}건 (이름만)"] + [f"- {_inert(name)}" for name in names]
        current = ""
        for line in lines:
            line = line[:_PART_LIMIT]
            if current and len(current) + 1 + len(line) > _PART_LIMIT:
                parts.append(current)
                current = ""
            current = f"{current}\n{line}" if current else line
        parts.append(current)
    return parts


def _open_body_thread(card: dict, subject: str) -> dict[str, str]:
    """A new thread for this card's full text, beside the digest thread.

    Discord does not nest threads, and the card already sits inside the digest thread,
    so the full-text thread opens in that thread's parent channel through the shared
    ``origin_notice.resolve_thread_id`` (name ``📄 전문 · <subject>``, big title first).
    """
    channel = triage_confirm.fetch_channel(str(card["channel_id"]))
    is_thread = isinstance(channel, dict) and channel.get("type") in _THREAD_TYPES
    parent = str(channel.get("parent_id") or "") if is_thread else ""
    origin_notice = triage_confirm._origin_notice()  # noqa: SLF001 — interop runtime import
    label = f"{BODY_THREAD_LABEL} · {' '.join(subject.split())[:_SUBJECT_LIMIT]}"
    thread_id = origin_notice.resolve_thread_id(
        triage_confirm._api,  # noqa: SLF001 — shared rate-limited Discord client
        origin_notice.OriginRef(channel_id=parent or str(card["channel_id"])), label,
    )
    guild = str(channel.get("guild_id") or "") if isinstance(channel, dict) else ""
    return {"thread_id": str(thread_id), "guild_id": guild, "stage": "opened"}


def body_link_line(thread: dict[str, str], subject: str) -> str:
    """The one line under the card that points at its full-text thread."""
    try:
        triage_confirm._origin_notice()  # noqa: SLF001 — puts the interop runtime on the path
        from automation.interop.owner_message import discord_link
    except ImportError:
        url = None
    else:
        url = discord_link(space="guild" if thread["guild_id"] else "unknown",
                           channel_id=thread["thread_id"],
                           guild_id=thread["guild_id"] or None).url
    where = url or f"스레드 「📄 {BODY_THREAD_LABEL} · {' '.join(subject.split())[:_SUBJECT_LIMIT]}」"
    return _inert(f"📄 전문은 별도 스레드에 있습니다: {where}")


def _show_body(card: dict) -> str:
    """📄: open the card's own thread, point at it under the card, then post the text there.

    Each step is recorded before the next (``digest_body_threads.stage``), so a retry
    after a failure resumes where it stopped and never opens a second thread.
    """
    db = triage_gate.db_path()
    message_id = str(card["message_id"])
    thread = triage_store.digest_body_thread(db, message_id)
    if thread is not None and thread["stage"] == "posted":
        return "done"
    mail = triage_transport._get_mail(str(card["uid"]))  # noqa: SLF001 — the digest's read path
    subject = str(card.get("subject") or mail.get("subject") or "")
    if thread is None:
        thread = _open_body_thread(card, subject)
        triage_store.save_digest_body_thread(db, message_id, thread, triage_core.utc_now())
    if thread["stage"] == "opened":
        triage_confirm.post_in(str(card["channel_id"]), body_link_line(thread, subject),
                               reply_to=message_id)
        thread = {**thread, "stage": "linked"}
        triage_store.save_digest_body_thread(db, message_id, thread, triage_core.utc_now())
    parts = body_parts(mail, subject)
    posted = 0
    try:
        for part in parts:
            triage_confirm.post_in(thread["thread_id"], part)
            posted += 1
    except Exception:
        if not posted:
            raise
    # Some or all of the text is in the thread now — a retry would show it twice.
    triage_store.save_digest_body_thread(
        db, message_id, {**thread, "stage": "posted"}, triage_core.utc_now(),
    )
    return "done" if posted == len(parts) else "partial"


def _relink(card: dict) -> bool:
    """A repeat 📄 on a card whose text is already in its thread: the link once more."""
    db = triage_gate.db_path()
    message_id = str(card["message_id"])
    thread = triage_store.digest_body_thread(db, message_id)
    if thread is None or thread["stage"] != "posted":
        return False
    if triage_store.digest_action_status(db, message_id, "body") not in ("done", "partial"):
        return False
    triage_confirm.post_in(str(card["channel_id"]),
                           body_link_line(thread, str(card.get("subject") or "")),
                           reply_to=message_id)
    return True


def _pending_draft(uid: str) -> dict | None:
    drafts = [
        draft for draft in triage_gate.list_drafts()
        if draft.get("uid") == uid and draft.get("status") == "pending"
    ]
    return max(drafts, key=lambda draft: str(draft.get("created_at") or ""), default=None)


def _card_link(draft: dict) -> str:
    try:
        from automation.interop.thread_pointer import approval_thread_line
    except ImportError:
        return ""
    match = _URL.search(approval_thread_line("draft", str(draft["id"]), draft))
    return match[1] if match and match[1] != "unavailable" else ""


def draft_preview(draft: dict, *, existed: bool) -> str:
    """The ✍️ reply in the digest thread: who/what, the body, and where to approve it."""
    body = str(draft.get("body") or "").strip()
    if len(body) > _PREVIEW_LIMIT:
        body = body[: _PREVIEW_LIMIT - 1] + "…"
    quoted = "\n".join(f"> {line}" if line else ">" for line in body.splitlines()) or "> (빈 본문)"
    link = _card_link(draft)
    where = f"승인 카드: {link}" if link else f"승인 카드: 승인 스레드에서 draft `{draft['id']}` 검색"
    head = "이 메일의 회신 초안이 이미 있습니다" if existed else "회신 초안을 썼습니다"
    return _inert("\n".join([
        f"✍️ **{head}** · draft `{draft['id']}`",
        f"받는 사람 {draft.get('to') or '(없음)'} · 제목 {draft.get('subject') or '(없음)'}",
        quoted,
        f"-# 아직 보내지 않았습니다. 보내려면 {where} 에서 ✅, 버리려면 ⛔.",
    ]))[:2000]


def _write_draft(card: dict, attempt: int = 1) -> str:
    """Draft through the owner-instruction path, under the reply-request lock."""
    import fcntl

    import triage_cli

    uid = str(card["uid"])
    lock_dir = triage_gate.gate_dir()
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (lock_dir / "reply-request.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existed = triage_gate.has_draft_for(uid)
        if not existed:
            if attempt == 1:  # the model call takes a while — say it started, once
                triage_confirm.post_in(str(card["channel_id"]), DRAFT_ACK,
                                       reply_to=str(card["message_id"]))
            triage_cli.cmd_draft(argparse.Namespace(
                uid=uid, instruction=DRAFT_INSTRUCTION, attachment=[], no_post=False,
                with_evidence=False, reply_all=False,
                digest_card={"channel_id": card["channel_id"], "message_id": card["message_id"]},
            ))
    draft = _pending_draft(uid)
    if draft is None:  # a draft exists but is no longer pending (sent/discarded)
        triage_confirm.post_in(
            str(card["channel_id"]),
            "✍️ 이 메일의 초안은 이미 처리됐습니다(발송 또는 폐기). 새 초안은 이 메시지에 답장으로 요청해 주세요.",
            reply_to=str(card["message_id"]),
        )
        return "done"
    triage_confirm.post_in(str(card["channel_id"]), draft_preview(draft, existed=existed),
                           reply_to=str(card["message_id"]))
    return "done"


def _run(action: str, card: dict, attempt: int) -> str:
    if action == "draft":
        return _write_draft(card, attempt)
    return _show_body(card)


def _gave_up(card: dict, action: str) -> None:
    """The last allowed attempt failed — say so under the card (best-effort)."""
    try:
        triage_confirm.post_in(
            str(card["channel_id"]),
            f"⚠️ {_ACTION_LABEL[action]}을 {MAX_ATTEMPTS}번 시도했지만 가져오지 못했습니다. "
            "이 카드에 답장으로 다시 요청해 주세요.",
            reply_to=str(card["message_id"]),
        )
    except Exception as error:  # noqa: BLE001 — the notice must never fail the tick
        print(f"DIGEST-REACTION-NOTICE-FAIL action={action} err={type(error).__name__}",
              file=sys.stderr)


def _act(card: dict, owner: str, db, *, pressed: str = "") -> int:
    """Run each new owner reaction on one card once; return actions run.

    ``pressed`` is the action the gateway just heard: a 📄 pressed again on a card whose
    text is already in its thread gets the thread link again instead of nothing.
    """
    try:
        actions = _owner_reactions(card, owner)
    except HTTPError as error:
        if error.code == 429:
            raise
        print(f"DIGEST-REACTION-READ-FAIL err={type(error).__name__}", file=sys.stderr)
        return 0
    except Exception as error:  # noqa: BLE001 — one card's read never stops the others
        print(f"DIGEST-REACTION-READ-FAIL msg={card['message_id']} "
              f"err={type(error).__name__}", file=sys.stderr)
        return 0
    ran = 0
    for action in actions:
        stamp = triage_core.utc_now()
        attempt = triage_store.claim_digest_action(
            db, str(card["message_id"]), action, stamp, max_attempts=MAX_ATTEMPTS,
        )
        if not attempt:
            if action == "body" and pressed == "body" and _relink(card):
                print(f"DIGEST-REACTION action=body item={card['item_no']} status=relinked")
            continue
        try:
            status = _run(action, card, attempt)
        except Exception as error:  # noqa: BLE001 — retried on a later tick, capped
            triage_store.finish_digest_action(
                db, str(card["message_id"]), action, "retry", triage_core.utc_now(),
            )
            print(f"DIGEST-REACTION-FAIL action={action} msg={card['message_id']} "
                  f"err={triage_core.redact(str(error))[:160]}", file=sys.stderr)
            if attempt >= MAX_ATTEMPTS:
                _gave_up(card, action)
            continue
        triage_store.finish_digest_action(
            db, str(card["message_id"]), action, status, triage_core.utc_now(),
        )
        ran += 1
        print(f"DIGEST-REACTION action={action} item={card['item_no']} status={status}")
    return ran


def _recent_cards(now: datetime | None) -> tuple[object, list[dict]]:
    db = triage_gate.db_path()
    current = now or datetime.now(UTC)
    return db, triage_store.recent_digest_cards(db, since=(current - WINDOW).isoformat())


def run_tick(*, now: datetime | None = None) -> int:
    """Safety net: act once on each new owner reaction on recent digest cards."""
    db, cards = _recent_cards(now)
    if not cards:
        return 0
    owner = triage_confirm.owner_id()
    ran = 0
    for card in cards:
        try:
            ran += _act(card, owner, db)
        except HTTPError as error:
            if error.code != 429:
                raise
            break  # The shared REST client already backed off; leave the rest for later.
    return ran


def run_card(message_id: str, *, now: datetime | None = None, pressed: str = "") -> int:
    """The gateway heard a press on ``message_id`` — act on that one card right away.

    The reaction is read back from Discord before acting (the event only says that
    someone pressed), so the owner-only and once-per-action rules are the tick's own.
    A message that is not a recent digest card is ignored without any Discord call.
    """
    db, cards = _recent_cards(now)
    card = next((card for card in cards if str(card["message_id"]) == message_id), None)
    if card is None:
        return 0
    return _act(card, triage_confirm.owner_id(), db, pressed=pressed)

"""Daily owner digest: classify actions, summarize, deliver, then record success."""

from __future__ import annotations

import re
import sys

from contextvars import ContextVar
from dataclasses import replace

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import digest_reaction_actions
import mail_contacts
import triage_confirm
import triage_core
import triage_gate
import triage_llm
import triage_llm_routing
import triage_recipient
import triage_store
import triage_transport

SUMMARY_FALLBACK = "(요약 실패)"
_CATEGORY_BADGES = {"important": "🔴 중요", "normal": "🔵 일반", "spam": "🗑️ 스팸"}
_FLAG_BADGES = {
    "reply_needed": "↩️ 회신 필요",
    "replied": "📤 회신함",
    "schedule_needed": "📅 일정",
    "budget": "💳 예산",
    "cc": "👀 참조(CC)",
    "classification_failed": "⚠️ 분류 실패",
}
_CLASSIFY_FAILED_FLAG = "classification_failed"
# Fail-open classification: surface the mail conservatively as 중요 with NO
# actionable flags, so one unparseable/slow model response never strands the
# whole digest and never delegates a calendar draft off a fabricated verdict.
_CLASSIFY_FALLBACK = triage_core.Classification(
    category="important",
    reply_needed=False,
    schedule_needed=False,
    budget=False,
    schedule_text="",
    reason="classification_unavailable",
)
_MARKDOWN_ESCAPE = re.compile(r"([\\*_~`|\[\]])")
# Per-message budget: Discord rejects >2000 chars, and the gateway shows the agent
# only the first 500 chars of a replied-to message, so the reply key sits right
# under the (clipped) subject heading.
_SUBJECT_LIMIT = 200
_SUMMARY_LIMIT = 1200
# Newest state.db rows (all folders) searched for the owner's own replies.
_SENT_LOOKUP_LIMIT = 300


def _footer() -> str:
    """How to reply. Never asks for a ✅ here — approval is the draft's own card."""
    return (
        "---\n"
        "👆 메일 메시지에서 📄 를 누르면 그 메일 전문, ✍️ 를 누르면 회신 초안이 같은 스레드에 올라옵니다\n"
        "💬 회신 지시 · 스레드의 메일 메시지에 **답장(Reply)** 으로 요지를 적으면 "
        '그 메일의 회신 초안을 만듭니다 ("N번 메일, …라고 회신해줘"도 됩니다)\n'
        "초안은 정식 승인 카드(요청별 승인 스레드)로 올라오고, 에이전트 답장에 그 카드 링크가 실립니다"
    )


def thread_name(kst_now: datetime, count: int) -> str:
    return f"📬 기관메일 다이제스트 {kst_now.astimezone(ZoneInfo('Asia/Seoul')):%m-%d %H:%M} · {count}건"


def reply_key(kst_now: datetime, item_no: int) -> str:
    """Visible per-mail reply handle, e.g. ``1008-0800-3`` (stored in digest_items)."""
    return f"{kst_now.astimezone(ZoneInfo('Asia/Seoul')):%m%d-%H%M}-{item_no}"


def select_new_mails(
    mails: list[dict], digested: set[str], processed: set[str]
) -> list[dict]:
    """Keep wrapper-listed mails seen in no earlier digest/triage, oldest first.

    Pure: excludes uids present in either set and sorts ascending by the
    wrapper `date` field (ISO-8601 recv_date — lexicographic sort is
    chronological), stable for equal dates.
    """
    known = digested | processed
    fresh = [
        mail
        for mail in mails
        if str(mail.get("uid") or "") and str(mail.get("uid")) not in known
    ]
    return sorted(fresh, key=lambda mail: str(mail.get("date") or ""))


#: Set by ``run_digest`` while it builds a delivered digest: the calendar card must reply
#: under its mail card, which does not exist yet, so the event is handed over after posting.
_DEFER_SCHEDULE: ContextVar[bool] = ContextVar("digest_defer_schedule", default=False)


def build_item(
    mail_detail: dict, item_no: int, *,
    digest_day: str = "",
) -> tuple[dict, dict]:
    """Classify and summarize a mail, preserving the same text in delivery and storage.

    Calendar delegation still uses its own owner approval. Per-request model failures
    fall back per item; an unavailable account model chain fails the tick closed.
    """
    uid = str(mail_detail.get("uid") or "")
    subject = str(mail_detail.get("subject") or "")
    sender = str(mail_detail.get("sender") or "")
    body = str(mail_detail.get("body") or "")
    uid_opaque = triage_core.mask_value(uid)

    def classify_step() -> tuple[triage_core.Classification, str]:
        return triage_llm.classify(
            subject=subject, sender=sender, body=body,
            uid_opaque=uid_opaque,
            prompt_path=triage_transport._env_path(
                "TRIAGE_CLASSIFY_PROMPT",
                str(triage_transport.SKILL_DIR / "prompts/triage-classify-v1.md"),
            ),
        )

    try:
        cls, _provider = triage_llm_routing.call_with_retry(
            classify_step,
            retry_on=(triage_llm.LlmCallError, triage_core.LlmParseError),
        )
    except triage_llm.LlmUnavailableError as error:  # tier down — never a per-item fallback
        triage_llm.log_failure(
            purpose="classify", uid_opaque=uid_opaque,
            error=error,
        )
        raise
    except (triage_llm.LlmCallError, triage_core.LlmParseError) as error:
        cls, classify_failed = _CLASSIFY_FALLBACK, True
        triage_llm.log_failure(
            purpose="classify", uid_opaque=uid_opaque,
            error=error,
        )
    else:
        classify_failed = False

    def summarize_step() -> str:
        return triage_llm.summarize(
            subject=subject, sender=sender, body=body,
            uid_opaque=uid_opaque,
            prompt_path=triage_transport._env_path(
                "TRIAGE_DIGEST_PROMPT",
                str(triage_transport.SKILL_DIR / "prompts/digest-summary-v1.md"),
            ),
        )

    try:
        summary = triage_llm_routing.call_with_retry(summarize_step, retry_on=(Exception,))
    except triage_llm.LlmUnavailableError as error:  # tier down — never a per-item fallback
        triage_llm.log_failure(
            purpose="digest_summary", uid_opaque=uid_opaque,
            error=error,
        )
        raise
    except Exception as error:  # noqa: BLE001 — fail-open listing: keep the item, mark the failure
        summary = SUMMARY_FALLBACK
        triage_llm.log_failure(  # cron drops stderr — the log line is the only trace
            purpose="digest_summary", uid_opaque=uid_opaque,
            error=error,
        )
    owner = triage_recipient.owner_address()
    role = triage_recipient.recipient_role(body, owner)
    _to_addresses, cc_addresses = triage_recipient.parse_recipients(body)
    cc_display = ""
    if body.startswith("---"):
        for line in body.split("---", 2)[1].splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() == "cc":
                cc_display = mail_contacts.yaml_scalar(value)
                break
    if role == "cc" and cls.reply_needed:  # 참조 수신 — 회신 대상 아님 (owner 2026-07-19)
        cls = replace(cls, reply_needed=False)
    mass_notice = bool(re.search(r"(?:^|[<\s])no-?reply@", sender, re.IGNORECASE)) and any(
        marker in f"{subject}\n{body}".lower()
        for marker in ("newsletter", "뉴스레터", "bulk", "distribution", "수신 거부", "구독 해지")
    )
    if role == "to" and mass_notice and cls.reply_needed:
        cls = replace(cls, reply_needed=False)
    # The owner already answered this thread after it arrived (owner 2026-10-15).
    replied_at = str(mail_detail.get("replied_at") or "") if cls.reply_needed else ""
    if replied_at:
        cls = replace(cls, reply_needed=False)
    flags = cls.flags() + (("replied",) if replied_at else ()) + (("cc",) if role == "cc" else ())
    if classify_failed:
        flags = flags + (_CLASSIFY_FAILED_FLAG,)
    schedule = triage_transport.ScheduleNote("")
    schedule_text = cls.schedule_text if cls.category == "important" and cls.schedule_needed else ""
    deferred = bool(schedule_text) and _DEFER_SCHEDULE.get()
    if schedule_text and not deferred:
        schedule = triage_transport._delegate_schedule(schedule_text, uid_opaque, digest_day)
    shared = {
        "item_no": item_no,
        "uid": uid,
        "sender_masked": triage_core.mask_value(sender),
        "category": cls.category,
        "note": schedule.note,
        "recv_date": str(mail_detail.get("date") or ""),
    }
    dm_item = {
        **shared,
        "subject": subject,
        "sender": sender,
        "cc": cc_display if role != "unknown" else cc_addresses,
        "summary": summary,
        "flags": flags,
        "replied_at": replied_at,
        "calendar_card": schedule.card_url,
        "calendar_when": _event_when(schedule.start, schedule.end),
        "calendar_title": schedule.title,
        **({"schedule_text": schedule_text} if deferred else {}),
    }
    store_item = {
        **shared,
        "subject": subject,
        "summary": summary,
        "flags": ",".join(flags),
    }
    return dm_item, store_item


def _sanitize_inline(text: str) -> str:
    """One-line, markdown-inert projection of mail-derived text for the DM.

    Collapses whitespace/newlines, backslash-escapes Discord markdown, and
    inserts U+200B after every ``@`` so ``@everyone``/``@here``/``<@id>``
    can never ping from a digest card.
    """
    flat = " ".join(text.split())
    return _MARKDOWN_ESCAPE.sub(r"\\\1", flat).replace("@", "@\u200b")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _kst_stamp(stamp: str) -> str:
    """``MM-DD HH:MM`` in KST; '' when unparseable (fail-safe)."""
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # wrapper dates are UTC
    return parsed.astimezone(ZoneInfo("Asia/Seoul")).strftime("%m-%d %H:%M")


def _recv_kst(recv_date: str) -> str:
    """``수신 MM-DD HH:MM`` KST segment; '' when unparseable (fail-safe)."""
    stamp = _kst_stamp(recv_date)
    return f"수신 {stamp}" if stamp else ""


def _badge_line(item: dict) -> str:
    """Category → flags as Korean emoji badges (raw-key fail-safe)."""
    badges = [_CATEGORY_BADGES.get(str(item["category"]), str(item["category"]))]
    for flag in item["flags"]:
        badge = _FLAG_BADGES.get(str(flag), str(flag))
        stamp = _kst_stamp(str(item.get("replied_at") or "")) if flag == "replied" else ""
        badges.append(f"{badge} · {stamp}" if stamp else badge)
    return " · ".join(badges)


def _owner_replied_at(mail: dict, sent_mails: list[dict]) -> str:
    """Date of the owner's first reply to this mail's sender in the same thread, or ''.

    A candidate whose body cannot be read is skipped, so a read failure leaves
    ``회신 필요`` in place rather than hiding it.
    """
    candidates = triage_recipient.reply_candidates(
        str(mail.get("subject") or ""), str(mail.get("date") or ""), sent_mails
    )
    for sent in candidates:
        try:
            body = str(triage_transport._get_mail(str(sent.get("uid") or "")).get("body") or "")
        except triage_gate.GateError:
            continue
        if triage_recipient.addressed_to(body, str(mail.get("sender") or "")):
            return str(sent.get("date") or "")
    return ""


def render_item(item: dict, *, kst_now: datetime) -> str:
    """One mail as one Discord message: heading, reply key, badges, summary, people.

    Internal identifiers (uid, sender hash) stay in ``digest_items`` — the reader
    sees names only, and Cc collapses to ``A, B, C 외 N명`` without the owner.
    """
    subject = _clip(_sanitize_inline(str(item["subject"])), _SUBJECT_LIMIT)
    lines = [
        f"### {item['item_no']}. {subject}",
        f"-# 📄 누르면 전문 · ✍️ 누르면 회신 초안 · ↩️ 답장하면 지시대로 초안 · "
        f"회신 키 `{reply_key(kst_now, int(item['item_no']))}`",
        _badge_line(item),
        f"> 요약 · {_clip(_sanitize_inline(str(item['summary'])), _SUMMARY_LIMIT)}",
    ]
    people = [_recv_kst(str(item["recv_date"]))]
    sender = mail_contacts.sender_label(str(item.get("sender") or ""))
    if sender:
        people.append(f"발신 {_sanitize_inline(sender)}")
    people = [part for part in people if part]
    if people:
        lines.append(" · ".join(people))
    raw_cc = item.get("cc") or ""
    cc = mail_contacts.cc_label(
        tuple(raw_cc) if isinstance(raw_cc, (tuple, list)) else str(raw_cc),
        owner=triage_recipient.owner_address(),
    )
    if cc:
        lines.append(f"참조 {_sanitize_inline(cc)}")
    if item["note"]:
        lines.append(_schedule_line(item))
    return "\n".join(lines)


_WEEKDAY_KO = "월화수목금토일"


def _event_when(start: str, end: str) -> str:
    """``10/19(월) 13:30–14:30`` in KST, or '' when the calendar did not say."""
    try:
        begin = datetime.fromisoformat(start).astimezone(ZoneInfo("Asia/Seoul"))
    except ValueError:
        return ""
    when = f"{begin.month}/{begin.day}({_WEEKDAY_KO[begin.weekday()]}) {begin:%H:%M}"
    try:
        finish = datetime.fromisoformat(end).astimezone(ZoneInfo("Asia/Seoul"))
    except ValueError:
        return when
    return f"{when}–{finish:%H:%M}"


def _schedule_line(item: dict) -> str:
    """The calendar line: a link to the approval card and the event, never a bare hash.

    A failure cause (``calendar-refused`` …) is shown as before so the owner sees why
    no card exists. Without a link (an older calendar skill) the line names the thread.
    """
    note = str(item["note"])
    if not note.startswith("calendar:"):
        cause = note.replace("`", "'")
        return f"🗓️ 일정 초안 `{cause}`"
    url = str(item.get("calendar_card") or "")
    head = (f"🗓️ 일정 초안 → [승인 카드](<{url}>)" if url
            else "🗓️ 일정 초안 · 승인 카드는 그날 「캘린더 · 다이제스트」 스레드")
    event = " ".join(part for part in (
        str(item.get("calendar_when") or ""),
        _clip(_sanitize_inline(str(item.get("calendar_title") or "")), _SUBJECT_LIMIT),
    ) if part)
    return f"{head} · {event}" if event else head


def render_digest_parts(dm_items: list[dict], *, kst_now: datetime) -> list[str]:
    """The digest as Discord messages: a header (with the reply how-to), then one per mail.

    Splitting on mail boundaries means no card is ever cut mid-line by the
    transport's 2000-char chunker, and each mail message is a reply target.
    """
    stamp = kst_now.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    header = ["## 📬 기관메일 다이제스트", f"{stamp} KST · 신규 {len(dm_items)}건"]
    if not dm_items:
        return ["\n".join([*header, "", "신규 메일 없음"])]
    header_text = "\n".join([*header, "메일별 카드는 이 메시지의 스레드에 이어집니다.", "", _footer()])
    return [header_text, *(render_item(item, kst_now=kst_now) for item in dm_items)]


def thread_intro(dm_items: list[dict], *, kst_now: datetime) -> str:
    """The digest's first message inside its own approval thread (owner Q187, 2026-10-16).

    The thread's name, big title and pending notice come from the standard request path;
    this says what arrived and how to act on a card. Never asks for a ✅ — decisions are
    made on the approval cards themselves, which reply under their mail card here.
    """
    stamp = kst_now.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    counts = [
        (label, sum(1 for item in dm_items if flag in item["flags"]))
        for flag, label in (("reply_needed", "회신 필요"), ("schedule_needed", "일정"), ("budget", "예산"))
    ]
    tally = " · ".join(f"{label} {count}" for label, count in counts if count)
    return "\n".join([
        f"{stamp} KST · 신규 {len(dm_items)}건" + (f" ({tally})" if tally else ""),
        "👇 메일 카드에서 📄 전문 · ✍️ 회신 초안 · 답장하면 그 말대로 초안",
        "회신 초안과 일정의 승인 카드는 그 메일 카드 아래 답글로 이 스레드에 올라옵니다",
    ])


def render_digest_dm(dm_items: list[dict], *, kst_now: datetime) -> str:
    """The whole digest as one text (dry-run preview); messages are blank-line joined."""
    return "\n\n".join(render_digest_parts(dm_items, kst_now=kst_now))


def _fail_marker(stage: str, code: str, error: BaseException) -> str:
    """One redacted, single-line machine marker for a digest failure.

    The cron watcher keys retry/alert policy on ``stage``/``code``/``retry_safe``
    fields, never on free-form prose. ``detail`` is redacted (emails, long
    digits) and flattened so no mail content, address, or token can leak into
    the owner failure alert. Both current failure boundaries are retry-unsafe:
    a build-stage item may already have delegated a calendar draft, and a
    delivery failure may have sent some Discord chunks.
    """
    detail = triage_core.redact(str(error)).replace("\n", " ")[:200]
    return f"DIGEST-FAIL stage={stage} retry_safe=false code={code} detail={detail}"


def _flat_channel() -> str:
    """The channel the cards went to when no thread opened, or '' when unknown."""
    try:
        return triage_confirm._owner_channel()  # noqa: SLF001 — the channel dm_owner posted to
    except Exception as error:  # noqa: BLE001 — reactions are a convenience, the digest stands
        print(f"DIGEST-REACTION-CHANNEL-FAIL err={type(error).__name__}", file=sys.stderr)
        return ""


def _attach_reactions(channel_id: str, message_id: str) -> None:
    """Pre-attach 📄·✍️ to one card; a failure leaves the card readable and is logged."""
    if not channel_id or not message_id:
        return
    try:
        digest_reaction_actions.attach(channel_id, message_id)
    except Exception as error:  # noqa: BLE001 — never fail a delivered digest over an emoji
        print(f"DIGEST-REACTION-ATTACH-FAIL msg={message_id} err={type(error).__name__}",
              file=sys.stderr)


def _open_thread(kst_now: datetime, count: int) -> str:
    """Today's standard approval thread id, or '' to keep the older flat layout."""
    try:
        import triage_digest_thread

        return str(triage_digest_thread.open_for(kst_now, count).channel_id)
    except Exception as error:  # noqa: BLE001 — the digest must still reach the owner
        print(f"DIGEST-THREAD-FAIL stage=open err={type(error).__name__}", file=sys.stderr)
        return ""


def _deliver(parts: list[str], dm_items: list[dict], kst_now: datetime) -> tuple[list[str], str]:
    """Post the digest; ids are [header, card…] and the channel the cards went to."""
    thread_id = _open_thread(kst_now, len(dm_items)) if len(parts) > 1 else ""
    if thread_id:
        intro = thread_intro(dm_items, kst_now=kst_now)
        if parts[0].startswith("⚠️"):  # the sync warning stays the first thing seen
            intro = parts[0].split("\n", 1)[0] + "\n" + intro
        ids = [triage_confirm.post_in(thread_id, intro)]
    else:
        ids = [triage_confirm.dm_owner(parts[0])]
        thread_id = (
            triage_confirm.digest_thread(ids[0], thread_name(kst_now, len(dm_items)))
            if len(parts) > 1 else ""
        )
    ids.extend(
        triage_confirm.post_in(thread_id, part) if thread_id else triage_confirm.dm_owner(part)
        for part in parts[1:]
    )
    return ids, thread_id


def _delegate_after_post(
    dm_item: dict, store_item: dict, message_id: str, channel_id: str, *, kst_now: datetime,
) -> None:
    """Hand a mail's event to calendar now that its card exists, then show the result on it.

    Calendar posts its approval card as a reply under this mail card when both sit in the
    day's digest thread. A failed card edit leaves the card readable and is logged.
    """
    uid_opaque = triage_core.mask_value(str(dm_item["uid"]))
    schedule = triage_transport._delegate_schedule(
        str(dm_item["schedule_text"]), uid_opaque, kst_now.date().isoformat(),
        reply_to=message_id, reply_channel=channel_id,
    )
    dm_item.update(
        note=schedule.note, calendar_card=schedule.card_url,
        calendar_when=_event_when(schedule.start, schedule.end), calendar_title=schedule.title,
    )
    store_item["note"] = schedule.note
    if not (schedule.note and channel_id and message_id):
        return
    try:
        triage_confirm.edit_message(channel_id, message_id, render_item(dm_item, kst_now=kst_now))
    except Exception as error:  # noqa: BLE001 — the card stays; only its calendar line is late
        print(f"DIGEST-CARD-EDIT-FAIL msg={message_id} err={type(error).__name__}", file=sys.stderr)


def run_digest(*, limit: int, sync: bool, dry_run: bool) -> int:
    """One digest tick: list → select → build → DM first → record after.

    Zero new mail still sends the (empty) digest DM and records item_count=0.
    A sync gate failure falls back to local DB data and marks the DM/dry-run body
    with a warning that mailon reauthentication may be needed.
    Any build-stage or delivery failure raises a single redacted structured
    ``DIGEST-FAIL`` marker (see ``_fail_marker``) before ``record_digest_run``,
    leaving every listed mail undigested so the next tick retries. An
    unavailable Codex OAuth tier gets its own ``code=codex_unavailable`` marker:
    with no second tier to degrade to, the tick fails closed rather than
    delivering a digest of placeholders.
    """
    kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
    db = triage_gate.db_path()
    digested = triage_store.digested_uids(db)
    processed = {row[0] for row in triage_store.processed_rows(db)}
    sync_failed = False
    try:
        mails = triage_transport._list_mails(limit, sync)
    except triage_gate.GateError:
        if not sync:
            raise
        mails = triage_transport._list_mails(limit, False)
        sync_failed = True
    selected = select_new_mails(mails, digested, processed)
    try:
        sent_mails = triage_transport._sent_mails(_SENT_LOOKUP_LIMIT) if selected else []
    except Exception as error:  # noqa: BLE001 — fail-open: keep 회신 필요, say why on stderr
        sent_mails = []
        print(f"DIGEST-SENT-LOOKUP-FAIL {type(error).__name__}", file=sys.stderr)
    dm_items: list[dict] = []
    store_items: list[dict] = []
    for item_no, mail in enumerate(selected, start=1):
        uid = str(mail.get("uid") or "")
        detail = {**mail, **triage_transport._get_mail(uid), "uid": uid}
        detail["replied_at"] = _owner_replied_at(detail, sent_mails)
        defer = _DEFER_SCHEDULE.set(not dry_run)
        try:
            dm_item, store_item = build_item(detail, item_no, digest_day=kst_now.date().isoformat())
        except triage_llm.LlmUnavailableError as error:  # fail closed — no downgraded digest
            raise triage_gate.GateError(
                _fail_marker("build", "codex_unavailable", error), 4
            ) from error
        except Exception as error:  # noqa: BLE001 — cron alert needs one structured marker
            raise triage_gate.GateError(_fail_marker("build", "llm_call_failed", error), 4) from error
        finally:
            _DEFER_SCHEDULE.reset(defer)
        dm_items.append(dm_item)
        store_items.append(store_item)
    parts = render_digest_parts(dm_items, kst_now=kst_now)
    if sync_failed:
        parts[0] = "⚠️ mailon 동기화 실패 — 로컬 DB 기준 (재인증 필요할 수 있음)\n" + parts[0]
    if dry_run:
        print("\n\n".join(parts))
        print(f"DIGEST dry-run items={len(dm_items)} messages={len(parts)}")
        return 0
    try:  # Delivery first — failure leaves every mail undigested
        message_ids, thread_id = _deliver(parts, dm_items, kst_now)
    except Exception as error:  # noqa: BLE001 — cron alert needs one structured marker
        raise triage_gate.GateError(
            _fail_marker("deliver", "discord_delivery_failed", error), 4
        ) from error
    card_channel = thread_id or _flat_channel()
    for dm_item, store_item, message_id in zip(dm_items, store_items, message_ids[1:], strict=True):
        if dm_item.get("schedule_text"):
            _delegate_after_post(dm_item, store_item, str(message_id or ""), card_channel, kst_now=kst_now)
    for store_item, message_id in zip(store_items, message_ids[1:], strict=True):
        store_item["reply_key"] = reply_key(kst_now, int(store_item["item_no"]))
        store_item["message_id"] = str(message_id or "")
        store_item["channel_id"] = card_channel
        _attach_reactions(card_channel, str(message_id or ""))
    run_id = triage_store.record_digest_run(db, triage_core.utc_now(), store_items)
    print(f"DIGEST run={run_id} items={len(dm_items)}")
    return 0

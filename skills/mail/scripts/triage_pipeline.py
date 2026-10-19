"""Shared drafting pipeline used by the triage CLI's process/draft paths."""
from __future__ import annotations

import argparse
import importlib
import secrets
import sys
from dataclasses import replace

import mail_quote
import triage_approval
import triage_core
import triage_gate
import triage_llm
import triage_mode
import triage_recipient
import triage_store
from triage_transport import (
    SKILL_DIR,
    _delegate_schedule,
    _env_path,
    _get_mail,
    _list_mails,
)

mail_evidence = importlib.import_module("mail_evidence")

DEFAULT_REPLY_PROMPT = SKILL_DIR / "prompts/reply-draft-v2.md"


def _post_draft_for_approval(draft: dict, *, notice: str = "") -> str:
    """Post through the shared lifecycle: one live approval message per mail key."""
    return triage_approval.post_for_approval(draft, notice=notice)


def compose_and_post(
    to: str, subject: str, body: str, *, post: bool,
    attachments: tuple[str, ...] = (),
    cc: str = "",
    evidence_pack: object | None = None,
    origin_channel_id: str = "",
    origin_message_id: str = "",
    quote: str = "",
    forward: dict | None = None,
    drive_share: dict | None = None,
    bcc: tuple[str, ...] = (),
    quote_html: mail_quote.QuoteHtml | None = None,
) -> dict:
    recipient_body = (
        mail_evidence.sanitize_draft_body(body, evidence_pack)
        if evidence_pack is not None else body
    )
    draft = triage_gate.create_draft(
        uid=f"compose:{secrets.token_hex(8)}", sender="", mail_subject="",
        to=to, cc=cc, subject=subject, body=recipient_body,
        category="compose", flags=(),
        kind="compose",
        attachment_paths=attachments,
        origin_channel_id=origin_channel_id, origin_message_id=origin_message_id,
        quote=quote, forward=forward, drive_share=drive_share,
        **({"bcc": bcc} if bcc else {}),
        quote_html=quote_html,
    )
    recipients = ", ".join(item for item in (to, cc) if item)
    gap = triage_recipient.related_recipient_gap(
        recipients, subject, triage_gate.list_drafts(), now_utc=triage_core.utc_now())
    notice = ""
    if gap:
        shown = (f"{len(gap)}명 (주소 비공개)" if bcc else
                 ", ".join(gap[:5]) + (f" 외 {len(gap) - 5}명" if len(gap) > 5 else ""))
        notice = ("\n⚠️ 직전 관련 메일 수신자 중 제외됨: "
                  f"`{shown}` — 의도한 제외인지 확인 후 ✅ 해주세요")
    if evidence_pack is not None:
        notice += mail_evidence.owner_notice(evidence_pack)
    if post:
        _post_draft_for_approval(draft, notice=notice)
        draft = triage_gate.load_draft(draft["id"])
        import triage_supersede

        triage_supersede.supersede_older(draft)
    else:
        print(
            triage_core.render_approvals_message(
                draft,
                destination=triage_core.ApprovalRenderDestination.CONSOLE,
            )
            + notice
        )
    return draft


def _draft_and_post(
    detail: dict, cls, *, post: bool, instruction: str = "",
    attachments: tuple[str, ...] = (), evidence_pack: object | None = None,
    reply_all: bool = False, digest_card: dict | None = None,
) -> list[str]:
    actions: list[str] = []
    card = digest_card or {}
    to = triage_core.extract_reply_address(detail.get("sender") or "")
    if not to:
        return ["reply-no-address"]
    original = mail_quote.parse_original(detail)
    cc = (
        mail_quote.reply_all_cc(original, to=to, owner=triage_recipient.owner_address())
        if reply_all else ""
    )
    subject, body, _provider = triage_llm.draft_reply(
        subject=detail.get("subject") or "", sender=detail.get("sender") or "",
        body=detail.get("body") or "",
        uid_opaque=triage_core.mask_value(detail["uid"]),
        prompt_path=_env_path("TRIAGE_REPLY_PROMPT", str(DEFAULT_REPLY_PROMPT)),
        instruction=instruction,
        evidence=mail_evidence.prompt_block(evidence_pack) if evidence_pack is not None else "",
    )
    if evidence_pack is not None:
        body = mail_evidence.sanitize_draft_body(body, evidence_pack)
    draft = triage_gate.create_draft(
        uid=detail["uid"], sender=detail.get("sender") or "",
        mail_subject=detail.get("subject") or "", to=to, subject=subject, body=body,
         category=cls.category, flags=cls.flags(),
        attachment_paths=attachments, cc=cc, quote=mail_quote.render_quote(original),
        quote_html=mail_quote.render_quote_html(original),
        origin_channel_id=str(card.get("channel_id") or ""),
        origin_message_id=str(card.get("message_id") or ""),
    )
    actions.append(f"draft:{draft['id']}")
    if post:
        notice = mail_evidence.owner_notice(evidence_pack) if evidence_pack is not None else ""
        draft = {**draft, "message_id": _post_draft_for_approval(draft, notice=notice)}
        actions.append(f"posted:{draft['message_id']}")
    else:
        print(
            triage_core.render_approvals_message(
                draft,
                destination=triage_core.ApprovalRenderDestination.CONSOLE,
            )
        )
    return actions


def _classify(
    uid: str, detail: dict,
) -> triage_core.Classification:
    subject = detail.get("subject") or ""
    sender = detail.get("sender") or ""
    body = detail.get("body") or ""
    cls, _provider = triage_llm.classify(
        subject=subject, sender=sender, body=body,
        uid_opaque=triage_core.mask_value(uid),
        prompt_path=_env_path(
            "TRIAGE_CLASSIFY_PROMPT", str(SKILL_DIR / "prompts/triage-classify-v1.md")
        ),
    )
    return cls


def _process_one(uid: str, *, post: bool) -> tuple[str, str]:
    detail = _get_mail(uid)
    cls = _classify(uid, detail)
    if cls.category == "spam":
        return "spam-skip", cls.category
    if cls.category != "important":
        return "no-action", cls.category
    actions: list[str] = []
    role = triage_recipient.recipient_role(
        str(detail.get("body") or ""), triage_recipient.owner_address()
    )
    if role == "cc" and cls.reply_needed:  # 참조 수신 — 회신 대상 아님 (owner 2026-07-19)
        cls = replace(cls, reply_needed=False)
        actions.append("cc-no-reply")
    if cls.schedule_needed:
        if cls.schedule_text:
            actions.append(_delegate_schedule(cls.schedule_text, triage_core.mask_value(uid)).note)
        else:
            actions.append("calendar-no-text")
    if cls.reply_needed:
        actions += _draft_and_post({**detail, "uid": uid}, cls, post=post)  # ③④
    return ",".join(actions) or "logged", cls.category


def run_process(args: argparse.Namespace) -> int:
    if triage_mode.effective_mode() == "no-go":
        raise triage_gate.GateError("mail-mode=no-go — W4-2 파이프라인 비활성(W4-1N 분기)", 3)
    db = triage_gate.db_path()
    handled = 0
    for mail in _list_mails(args.limit, not args.no_sync):
        uid = str(mail.get("uid") or "")
        if not uid or triage_store.is_processed(db, uid) or triage_gate.has_draft_for(uid):
            continue
        if not triage_store.claim_mail(db, uid, triage_core.utc_now()):
            continue
        opaque = triage_core.mask_value(uid)
        try:
            action, category = _process_one(uid, post=not args.no_post)
        except Exception as error:  # noqa: BLE001 — release claim, keep the tick going
            triage_store.release_mail(db, uid)
            print(f"MAIL-FAIL uid={opaque} err={triage_core.redact(str(error))[:200]}",
                  file=sys.stderr)
            continue
        triage_store.record_processed(
            db, uid, category=category, action=action,
            processed_at=triage_core.utc_now(),
        )
        print(f"MAIL uid={opaque} category={category} action={action}")
        handled += 1
    print(f"PROCESSED n={handled}")
    return 0

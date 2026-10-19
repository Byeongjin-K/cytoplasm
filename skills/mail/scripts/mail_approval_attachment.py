"""Versioned long-mail presentation and same-message attachment integrity.

The attachment is a rendering of the already approved body and quote. Its
metadata is deliberately separate from the legacy action-hash inputs.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypedDict
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from mail_quote import with_quote

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import JsonValue

FORMAT: Final = "attachment-v1"


class AttachmentMetadata(TypedDict):
    filename: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BodyAttachment:
    """Exact UTF-8 body-plus-quote bytes, with a header-safe derived filename."""

    content: bytes

    @property
    def metadata(self) -> AttachmentMetadata:
        digest = hashlib.sha256(self.content).hexdigest()
        return {"filename": f"mail-body-{digest}.txt", "size": len(self.content), "sha256": digest}


def body_attachment(draft: Mapping[str, JsonValue]) -> BodyAttachment:
    return BodyAttachment(with_quote(str(draft["body"]), str(draft.get("quote") or "")).encode("utf-8"))


def summary(draft: Mapping[str, JsonValue], instruction: str) -> str:
    """Replay old summary bytes or select the new version over attachment-v1."""
    from automation.interop.approval_card import CardRenderError
    if draft.get("render_version") in ("4", "5", "6"):
        content = _summary_v4(draft, instruction, share=draft.get("render_version") in ("5", "6"))
        if draft.get("render_version") == "6" and draft.get("reply_format") == "html-test":
            from mail_reply_format import TEST_CARD_LINE

            title, rest = content.split("\n", 1)
            return f"{title}\n{TEST_CARD_LINE}\n{rest}"
        return content
    if draft.get("bcc"):
        return _summary_v3(draft, instruction)

    match draft.get("render_version", "1"):
        case "1" | "2":
            pass
        case "3":
            return _summary_v3(draft, instruction)
        case _:
            raise CardRenderError("unknown attachment card render version")
    metadata = body_attachment(draft).metadata
    return "\n".join((
        "[mail-triage] 발송 승인 요청 (SUMMARY)",
        f"- 제목: {draft['subject']}",
        f"- To: {draft['to']}",
        f"- Cc: {draft.get('cc') or '-'}",
        f"- draft: `{draft['id']}` sha256: `{draft['sha256']}`",
        f"- body sha256: `{metadata['sha256']}`",
        f"- action hash: `{draft.get('approval_action_hash') or draft['sha256']}`",
        "본문: 첨부 파일 참조",
        f"- 반응(기본): {instruction}",
    ))


def _summary_v4(draft: Mapping[str, JsonValue], instruction: str, *, share: bool = False) -> str:
    recipients = f"- To: {draft['to']}\n- Cc: {draft.get('cc') or '-'}"
    if draft.get("bcc"):
        from mail_bcc import summary as bcc_summary

        recipients = bcc_summary(str(draft["to"]), str(draft.get("cc") or ""),
                                 tuple(str(address) for address in draft["bcc"]))
    size = body_attachment(draft).metadata["size"]
    lines = ["📨 **메일 발송 승인**", f"- 제목: {draft['subject']}", recipients,
             f"- 본문: 첨부 파일 참조 ({size} bytes)"]
    spec = draft.get("drive_share")
    if share:  # v5: the long-body card showed neither the attachments nor the share
        attachments = draft.get("attachments") or []
        if isinstance(attachments, list) and attachments:
            lines.append(f"- 첨부: {len(attachments)}개")
            lines.extend(f"  - `{str(item.get('display_name', '')).replace('`', chr(39))}` · {item.get('size_bytes')} bytes"
                         for item in attachments if isinstance(item, Mapping))
    if share and isinstance(spec, dict):
        from mail_card_compact import INVITE_NOTICE, share_line

        lines.extend((share_line(spec), INVITE_NOTICE))
    return "\n".join((*lines, instruction))


def _summary_v3(draft: Mapping[str, JsonValue], instruction: str) -> str:
    """New card layout over the unchanged attachment-v1 byte/metadata contract."""
    from automation.interop import owner_message as om
    from automation.interop.approval_card import CardRenderError

    if not callable(getattr(om, "render", None)):
        raise CardRenderError("mail attachment envelope unavailable")
    metadata = body_attachment(draft).metadata
    here = om.Ref("self")
    recipients = f"To: {draft['to']}\nCc: {draft.get('cc') or '-'}"
    if draft.get("bcc"):
        from mail_bcc import summary as bcc_summary

        recipients = bcc_summary(str(draft["to"]), str(draft.get("cc") or ""),
                                 tuple(str(address) for address in draft["bcc"]))
    message = om.OwnerMessage(
        subject_key=str(draft["id"]), subject="메일 발송 승인",
        fact=(f"제목: {draft['subject']}\n{recipients}\n"
              f"본문: 첨부 파일 참조 ({metadata['size']} bytes)"),
        location=here, owner=om.Action("react", here, instruction),
        agent_next="승인된 메일만 발송", recovery="irreversible",
        detail=om.Approval(None, "메일을 발송하지 않음"), render_version="owner-ko-v2",
    )
    try:
        content = om.render(message, destination=here)
    except om.OwnerMessageError as error:
        raise CardRenderError("mail attachment envelope cannot render") from error
    return "\n".join((
        content,
        f"- draft: `{draft['id']}` sha256: `{draft['sha256']}`",
        f"- body sha256: `{metadata['sha256']}`",
        f"- action hash: `{draft.get('approval_action_hash') or draft['sha256']}`",
    ))


def upload_request(content: str, target: Request, attachment: BodyAttachment) -> Request:
    """Add multipart capability to the existing mail REST request boundary."""
    boundary = "mail-approval-" + secrets.token_hex(24)
    metadata = attachment.metadata
    payload = json.dumps({
        "content": content, "allowed_mentions": {"parse": []},
        "attachments": [{"id": 0, "filename": metadata["filename"]}],
    }, ensure_ascii=False).encode("utf-8")
    wire = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
        'Content-Type: application/json\r\n\r\n'
    ).encode() + payload + (
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; '
        f'filename="{metadata["filename"]}"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n'
    ).encode() + attachment.content + f"\r\n--{boundary}--\r\n".encode()
    return Request(target.full_url, data=wire, headers={
        **dict(target.header_items()), "Content-Type": f"multipart/form-data; boundary={boundary}",
    }, method="POST")


def matches(draft: Mapping[str, JsonValue], message: Mapping[str, JsonValue]) -> bool:
    """Verify persisted metadata, same-message declaration, then bounded bytes.

    Old inline cards need no attachment. Attachment cards fail closed on missing
    metadata, unreadable URLs, or mismatches; downloads never carry authorization headers.
    """
    if "approval_format" not in draft:
        return "approval_attachment" not in draft
    if draft["approval_format"] != FORMAT:
        return False
    expected = body_attachment(draft).metadata
    if draft.get("approval_attachment") != expected:
        return False
    match message.get("attachments"):
        case [{"filename": str(filename), "size": int(size), "url": str(url)}]:
            if filename != expected["filename"] or size != expected["size"]:
                return False
            try:
                if urlsplit(url).scheme not in {"http", "https"}:
                    return False
                request = Request(url, headers={"User-Agent": "DiscordBot (https://discord.com, 1.0)"})
                with urlopen(request, timeout=30) as response:  # noqa: S310 — Discord attachment URL, no credentials
                    data = response.read(size + 1)
            except (OSError, ValueError):
                return False
            return len(data) == size and hashlib.sha256(data).hexdigest() == expected["sha256"]
        case _:
            return False

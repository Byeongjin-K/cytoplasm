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

from mail_card_compact import INVITE_NOTICE
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
    if draft.get("render_version") == "4":
        return _summary_v4(draft, instruction)
    if draft.get("render_version") == "5":
        return _summary_v5(draft, instruction)
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


def _summary_v4(draft: Mapping[str, JsonValue], instruction: str) -> str:
    return "\n".join((*_summary_head(draft), instruction))


def _summary_head(draft: Mapping[str, JsonValue]) -> tuple[str, ...]:
    recipients = f"- To: {draft['to']}\n- Cc: {draft.get('cc') or '-'}"
    if draft.get("bcc"):
        from mail_bcc import summary as bcc_summary

        recipients = bcc_summary(str(draft["to"]), str(draft.get("cc") or ""),
                                 tuple(str(address) for address in draft["bcc"]))
    size = body_attachment(draft).metadata["size"]
    return ("📨 **메일 발송 승인**", f"- 제목: {draft['subject']}", recipients,
            f"- 본문: 첨부 파일 참조 ({size} bytes)")


def _summary_v5(draft: Mapping[str, JsonValue], instruction: str) -> str:
    """v4 plus what else leaves with the mail: attachment names and sizes, and the Drive share.

    A long body moves to the card's text file, and v4 dropped these lines with it, so the
    owner could not see the file a share link would carry (owner 2026-10-19 12:15 KST).
    """
    extra: list[str] = []
    attachments = draft.get("attachments") or []
    if isinstance(attachments, list) and attachments:
        extra.append(f"- 첨부: {len(attachments)}개")
        for item in attachments:
            if isinstance(item, Mapping):
                name = str(item.get("display_name", "")).replace("`", "'")
                extra.append(f"  - `{name}` · {item.get('size_bytes')} bytes")
    share = draft.get("drive_share")
    if isinstance(share, Mapping):
        name = str(share.get("source_name", "")).replace("`", "'")
        grantees = share.get("grantees")
        people = ", ".join(f"`{person}`" for person in grantees) if isinstance(grantees, list) else "-"
        extra.extend((
            f"- Drive 공유: `{name}` · {share.get('size_bytes')} bytes → {people} 보기 권한만 — 링크 공개 없음",
            INVITE_NOTICE,
            f"  - ✅ 하면 올리고 확인한 뒤 본문의 `{share.get('placeholder')}` 자리에 링크를 넣어 보냅니다. "
            "하나라도 실패하면 메일은 나가지 않습니다.",
        ))
    return "\n".join((*_summary_head(draft), *extra, instruction))


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

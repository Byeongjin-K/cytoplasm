"""Mail approval text for the original card layout."""
from __future__ import annotations

from enum import StrEnum


class ApprovalRenderDestination(StrEnum):
    """The output surface for an approval card."""

    CONSOLE = "console"
    OWNER_DM = "owner-dm"


def render_approvals_message(
    draft: dict,
    *,
    destination: ApprovalRenderDestination = ApprovalRenderDestination.CONSOLE,
    instruction: str = "",
) -> str:
    """Render reply content independently of topic."""
    if draft.get("bcc"):
        import mail_bcc

        lines = [
            "[mail-triage] 숨은참조 메일 발송 승인 요청",
            mail_bcc.summary(draft["to"], draft.get("cc") or "", tuple(draft["bcc"])),
            f"- 제목: `{draft['subject']}`",
            "- 본문:", "```", draft["body"], "```",
        ]
    elif draft.get("provider") == "gmail":
        lines = [
            "[mail-triage] Gmail 발송 승인 요청 (DM 확정)",
            f"- 발신 계정: `{draft['sender_account']}`",
            f"- 작업: `{draft['gmail_approval_snapshot']['action_kind']}`",
            f"- 수신자: `{draft['to']}`",
        ]
        if draft.get("cc"):
            lines.append(f"- Cc: `{draft['cc']}`")
        lines.extend([
            f"- 회신 대상: `{draft['reply_target'] or '-'}`",
            f"- 제목: `{draft['subject']}`",
            "- 본문:",
            "```",
            draft["body"],
            "```",
        ])
    elif draft.get("kind") == "compose":
        title = "메일 전달(FW)" if draft.get("forward") else "새 메일"
        lines = [
            f"[mail-triage] {title} 발송 승인 요청 (DM 확정)",
            f"- To: `{draft['to']}`",
        ]
        if draft.get("cc"):
            lines.append(f"- Cc: `{draft['cc']}`")
        lines.extend([
            f"- 제목: `{draft['subject']}`",
            "- 본문:",
            "```",
            draft["body"],
            "```",
        ])
    else:
        preview = draft["body"][:600]
        lines = [
            "[mail-triage] 수신메일 회신 발송 승인 요청",
            f"- 분류: {draft['category']} / 플래그: {', '.join(draft['flags']) or '-'}",
            f"- 발신(마스킹): `{draft['sender_masked']}`",
            f"- 원문 제목: {draft['mail_subject']}",
        ]
        if draft.get("cc"):
            lines.append(f"- Cc: `{draft['cc']}`")
        lines.extend([
            f"- 회신 제목: {draft['subject']}",
            "- 회신 본문:",
            "```",
            preview + ("…" if len(draft["body"]) > 600 else ""),
            "```",
        ])
    forward = draft.get("forward")
    if isinstance(forward, dict):
        source = {"kimm": "기관메일", "gmail": "Gmail"}.get(str(forward.get("source")), str(forward.get("source")))
        lines.append(
            f"- 전달 원문: {source} 원문(헤더+본문)이 발송 본문 하단에, "
            f"원 첨부 {forward.get('attachment_count', 0)}개가 바이트 그대로 붙습니다"
        )
    elif draft.get("quote_format") == "original":
        lines.append("- 원문 인용: 원본 서식 유지")
    elif draft.get("quote_format") == "text":
        lines.append("- 원문 인용: 포함 (원본 서식 없이 글자만)")
    elif draft.get("quote"):
        lines.append("- 원문 인용: 포함 (수신 메일 원문이 발송 본문 하단에 붙습니다)")
    attachments = draft.get("attachments") or []
    if attachments:
        if draft.get("provider") == "gmail":
            lines.append(f"- 첨부: {len(attachments)}개")
            for item in attachments:
                safe_name = str(item["display_name"]).replace("`", "'")
                lines.append(
                    f"  - `{safe_name}` · {item['size_bytes']} bytes · `{item['sha256']}`"
                )
        else:
            lines.append(f"- 첨부: {len(attachments)}개")
            for item in attachments:
                safe_name = str(item["display_name"]).replace("`", "'")
                lines.append(
                    f"  - `{safe_name}` · {item['size_bytes']} bytes · `{item['mime_type']}`"
                )
    share = draft.get("drive_share")
    if isinstance(share, dict):
        safe_file = str(share["source_name"]).replace("`", "'")
        people = ", ".join(f"`{person}`" for person in share["grantees"])
        lines.extend([
            f"- Drive 공유: `{safe_file}` · {share['size_bytes']} bytes · `{share['sha256']}`",
            f"  - 올릴 곳: `{'/'.join(share['folder'])}/{share['drive_name']}`",
            f"  - 보기 권한(reader)만: {people} — 링크 공개 없음",
            f"  - ✅ 하면 업로드·검증·공유 뒤 본문의 `{share['placeholder']}` 자리에 링크를 넣어 보냅니다. "
            "하나라도 실패하면 메일은 나가지 않습니다.",
        ])
    lines.append(f"- draft: `{draft['id']}` sha256: `{draft['sha256']}`")
    if draft.get("provider") == "gmail":
        lines.append(f"- action hash: `{draft['approval_action_hash']}`")
    if instruction:
        lines.append(f"- 반응(기본): {instruction}")
    return "\n".join(lines)

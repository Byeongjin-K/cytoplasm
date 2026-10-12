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
    if draft.get("provider") == "gmail":
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
    lines.append(f"- draft: `{draft['id']}` sha256: `{draft['sha256']}`")
    if draft.get("provider") == "gmail":
        lines.append(f"- action hash: `{draft['approval_action_hash']}`")
    if instruction:
        lines.append(f"- 반응(기본): {instruction}")
    return "\n".join(lines)

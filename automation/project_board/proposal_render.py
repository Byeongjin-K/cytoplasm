"""Approval card for one project proposal (owner envelope, append-only render versions)."""

from __future__ import annotations

from typing import Final

from .models import DEFAULT_GRADE, NEVER_EXPORTED, Proposal
from .proposals import ProposalRecord

RENDER_VERSION: Final = "project-proposal-v2"
RENDER_VERSIONS: Final = ("project-proposal-v1", "project-proposal-v2")
MAX_MESSAGE_CHARS: Final = 1900


class RenderError(ValueError):
    pass


def _todo_lines(proposal: Proposal, title_limit: int) -> list[str]:
    lines: list[str] = []
    for index, todo in enumerate(proposal.todos, start=1):
        title = todo.title if len(todo.title) <= title_limit else todo.title[: title_limit - 1] + "…"
        owner = todo.owner or "담당 미정"
        due = todo.due or "기한 미정"
        reason = f" ({todo.due_reason})" if todo.due_reason and title_limit >= 60 else ""
        lines.append(f"{index}. {title} — {owner} · {due}{reason}")
    return lines or ["(할 일 없음 — 요약만 기록)"]


def _grade_line(proposal: Proposal) -> str:
    grade = proposal.grade or DEFAULT_GRADE
    outside = "밖으로 내보내지 않음" if grade in NEVER_EXPORTED else "요약 메일 등에 포함될 수 있음"
    return f"보안 등급: {grade} — 이 업무의 할 일·이후 카드 전부에 같은 등급 · {outside} (바꾸려면 revise --grade)"


def _fact(
    record: ProposalRecord, proposal: Proposal, summary_limit: int, title_limit: int, version: str
) -> str:
    source = proposal.source
    summary = proposal.summary if len(proposal.summary) <= summary_limit else proposal.summary[:summary_limit] + "…"
    kind = "기관 메일" if source.kind == "mail" else "회의록"
    attachments = f" · 첨부 {len(source.attachments)}개" if source.attachments else ""
    model = f"{proposal.provider}/{proposal.model}" if proposal.provider else "미상"
    return "\n".join(
        (
            f"프로젝트: {proposal.project}" + (f" (담당 {proposal.project_owner})" if proposal.project_owner else ""),
            *((_grade_line(proposal),) if version != "project-proposal-v1" else ()),
            f"요약: {summary}",
            "할 일:",
            *_todo_lines(proposal, title_limit),
            f"근거: {kind} 「{source.subject or '(제목 없음)'}」 {source.date[:10]}{attachments}",
            f"분석 모델: {model} · 제안 {record.request_id} · {record.revision}판",
            f"판본: {version}",
        )
    )


def render_proposal_card(
    record: ProposalRecord, proposal: Proposal, *, render_version: str = RENDER_VERSION
) -> str:
    if render_version not in RENDER_VERSIONS:
        raise RenderError(f"unsupported proposal render version: {render_version}")
    for summary_limit, title_limit in ((600, 120), (300, 80), (160, 50), (80, 30)):
        content = _render_v1(record, proposal, summary_limit, title_limit, render_version)
        if len(content) <= MAX_MESSAGE_CHARS:
            return content
    raise RenderError("proposal card exceeds the postable length")


def _render_v1(
    record: ProposalRecord, proposal: Proposal, summary_limit: int, title_limit: int, version: str
) -> str:
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise RenderError("owner envelope unavailable") from error
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=record.request_id,
        subject="프로젝트 제안 검토",
        fact=_fact(record, proposal, summary_limit, title_limit, version),
        location=here,
        owner=om.Action(
            "react", here, "✅ 반영 / ⛔ 취소 — 고칠 점은 이 스레드에 답글로 쓰면 수정안으로 다시 올립니다"
        ),
        agent_next="✅ 뒤 이 제안의 할 일만 프로젝트 데이터에 반영하고 대시보드를 다시 만든다",
        recovery="not_applicable",
        detail=om.Approval(None, "프로젝트 데이터를 바꾸지 않는다"),
        render_version="owner-ko-v2",
    )
    try:
        lines = om.render(message, destination=here).splitlines()
    except om.OwnerMessageError as error:
        raise RenderError("owner envelope cannot render") from error
    return "\n".join((*lines[:-1], f"- action_hash: `{record.action_hash}`", lines[-1]))

"""New batch card; the original v1 renderer remains byte-frozen."""
from __future__ import annotations

from collections.abc import Sequence

from automation.interop import owner_message as om

from .choices import RENDER_VERSION, ROW_REACTIONS
from .model import RelocationRecord


def render_choices(members: Sequence[tuple[RelocationRecord, str]]) -> str:
    lines = [
        "각 줄의 원·네모·하트 중 선택 후 마지막 ✅ 한 번.",
        "선택 없음=이동, 중복 선택=유지. 승인 감지 때 선택을 고정합니다.",
    ]
    for number, (record, text) in enumerate(members, start=1):
        move, keep, delete = ROW_REACTIONS[number - 1]
        lines.extend((
            f"{number}. {text}",
            f"{move} 이동(기본) → {record.note_relpath}",
            f"{keep} 유지 / {delete} 삭제(agent·peer 양쪽 USER.md·MEMORY.md의 같은 글, 백업 보관)",
        ))
    lines.append(f"판본: {RENDER_VERSION}")
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=members[0][0].batch_hash or "",
        subject=f"메모리 정리 — 줄별 선택 {len(members)}건",
        fact="\n".join(lines), location=here,
        owner=om.Action("react", here, "✅ 선택 확정 / ⛔ 전부 취소"),
        agent_next="peer가 선택을 한 번 고정한 뒤 실행; 이동은 저장 확인 후, 삭제는 양 계정 백업 후 처리",
        recovery="not_applicable", detail=om.Approval(None, "원본 유지"),
        render_version="owner-ko-v2",
    )
    rendered = om.render(message, destination=here).splitlines()
    return "\n".join((*rendered[:-1], f"- action_hash: `{members[0][0].batch_hash}`", rendered[-1]))

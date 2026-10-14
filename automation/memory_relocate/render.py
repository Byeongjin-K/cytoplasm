from __future__ import annotations

from collections.abc import Sequence

from .model import RelocationRecord

RENDER_VERSION = "mc-reloc-render-v3"
BATCH_RENDER_VERSION = "mc-reloc-batch-render-v1"
MAX_MESSAGE_CHARS = 1900
_DESTINATION_LABELS = {"person": "사람 노트", "memory": "MEMORY.md 로"}


class RenderError(ValueError):
    pass


def render_relocation_approval(
    record: RelocationRecord, *, entry_text: str, render_version: str = RENDER_VERSION,
) -> str:
    match render_version:
        case "mc-reloc-render-v1":
            content = _render_v1(record, entry_text)
        case "mc-reloc-render-v2":
            content = _render_v2(record, entry_text)
        case "mc-reloc-render-v3":
            content = _render_v3(record, entry_text)
        case _:
            raise RenderError(f"unsupported relocation render version: {render_version}")
    if len(content) > MAX_MESSAGE_CHARS:
        raise RenderError("memory relocation approval exceeds the postable length")
    return content


def prepare_approval_card(record: RelocationRecord, entry_text: str) -> tuple[str, str]:
    """Choose a new card before opening a thread; never rewrite a stored version."""
    if record.render_version is not None:
        return record.render_version, render_relocation_approval(record, entry_text=entry_text, render_version=record.render_version)
    try:
        return RENDER_VERSION, render_relocation_approval(record, entry_text=entry_text)
    except RenderError:
        version = "mc-reloc-render-v1"
        return version, render_relocation_approval(record, entry_text=entry_text, render_version=version)


def prepare_batch_card(members: Sequence[tuple[RelocationRecord, str]]) -> tuple[str, str]:
    """One card for every record that shares a batch hash; a stored version is replayed as is."""
    if not members or len({record.batch_hash for record, _ in members}) != 1 or members[0][0].batch_hash is None:
        raise RenderError("a batch card needs members that share one batch hash")
    version = members[0][0].render_version or BATCH_RENDER_VERSION
    match version:
        case "mc-reloc-batch-render-v1":
            content = _render_batch_v1(members)
        case "mc-reloc-batch-render-v2":
            from .choice_render import render_choices
            from .choices import MAX_ROWS

            if len(members) > MAX_ROWS:
                raise RenderError("too many batch choice rows")
            content = render_choices(members)
        case _:
            raise RenderError(f"unsupported batch render version: {version}")
    if len(content) > MAX_MESSAGE_CHARS:
        raise RenderError("memory relocation batch approval exceeds the postable length")
    return version, content


def _render_batch_v1(members: Sequence[tuple[RelocationRecord, str]]) -> str:
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise RenderError("owner envelope unavailable") from error
    lines = ["USER.md·MEMORY.md 에서 옮길 항목 (빼고 싶은 줄은 그 번호 이모지를 누른 뒤 ✅):"]
    for number, (record, text) in enumerate(members, start=1):
        number = record.batch_index or number
        label = _DESTINATION_LABELS.get(record.destination or "", "참조 노트")
        lines += [f"{number}. [{label}] {text}", f"   → {record.note_relpath}"]
    lines += [
        f"회수 예상 문자 수: {sum(record.reclaimable_chars for record, _ in members)}",
        f"판본: {BATCH_RENDER_VERSION}",
    ]
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=members[0][0].batch_hash or "",
        subject=f"메모리 정리 — 사람 정보·처리 규칙 {len(members)}건",
        fact="\n".join(lines),
        location=here,
        owner=om.Action("react", here, "✅ 승인(번호를 누른 줄은 남김) / ⛔ 전부 취소"),
        agent_next=(
            "사람 노트는 저장·인제스트 확인 뒤, 규칙은 MEMORY.md 추가 확인 뒤 원래 자리에서 삭제;"
            " 사람 정보는 이후 recall(검색)로 조회"
        ),
        recovery="not_applicable",
        detail=om.Approval(None, "원본 유지"),
        render_version="owner-ko-v2",
    )
    try:
        rendered = om.render(message, destination=here).splitlines()
    except om.OwnerMessageError as error:
        raise RenderError("owner envelope cannot render") from error
    return "\n".join((*rendered[:-1], f"- action_hash: `{members[0][0].batch_hash}`", rendered[-1]))


def _render_v2(record: RelocationRecord, entry_text: str) -> str:
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise RenderError("owner envelope unavailable") from error
    if not callable(getattr(om, "render", None)):
        raise RenderError("owner envelope renderer unavailable")
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=record.note_relpath, subject="memory→Obsidian 재배치",
        fact=f"{entry_text}; 회수 예상 문자 수: {record.reclaimable_chars}; mc-reloc-render-v2",
        location=here, owner=om.Action("react", here, "✅ 승인 / ⛔ 취소"),
        agent_next="저장·인제스트 확인 후 자체 메모리(ambient) 삭제; 이후 recall(검색)로만 조회",
        recovery="not_applicable", detail=om.Approval(None, "원본 유지"),
    )
    try:
        lines = om.render(message, destination=here).splitlines()
        return "\n".join((*lines[:3], f"- action_hash: `{record.action_hash}`", *lines[3:]))
    except om.OwnerMessageError as error:
        raise RenderError("owner envelope cannot render") from error


def _render_v3(record: RelocationRecord, entry_text: str) -> str:
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise RenderError("owner envelope unavailable") from error
    if not callable(getattr(om, "render", None)):
        raise RenderError("owner envelope renderer unavailable")
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=record.note_relpath,
        subject="memory→Obsidian 재배치",
        fact=(
            "원본 메모리 항목:\n"
            f"{entry_text}\n"
            f"대상 노트: {record.note_relpath}\n"
            f"회수 예상 문자 수: {record.reclaimable_chars}\n"
            "판본: mc-reloc-render-v3"
        ),
        location=here,
        owner=om.Action("react", here, "✅ 승인 / ⛔ 취소"),
        agent_next="저장·인제스트 확인 후 자체 메모리(ambient) 삭제; 이후 recall(검색)로만 조회",
        recovery="not_applicable",
        detail=om.Approval(None, "원본 유지"),
        render_version="owner-ko-v2",
    )
    try:
        lines = om.render(message, destination=here).splitlines()
        return "\n".join((
            *lines[:-1],
            f"- action_hash: `{record.action_hash}`",
            lines[-1],
        ))
    except om.OwnerMessageError as error:
        raise RenderError("owner envelope cannot render") from error


def _render_v1(record: RelocationRecord, entry_text: str) -> str:
    """Frozen pre-envelope card, including its original marker."""
    return (
        "[memory→Obsidian 재배치 승인 | mc-reloc-render-v1]\n"
        "원본 메모리 항목\n"
        "───\n"
        f"{entry_text}\n"
        "───\n"
        f"- 대상 노트: `{record.note_relpath}`\n"
        f"- 회수 예상 문자 수: {record.reclaimable_chars}\n"
        f"- action_hash: `{record.action_hash}`\n\n"
        "승인(✅) 시 이 항목은 자체 메모리(ambient)에서 **삭제**되고 이후에는 "
        "**recall(검색)로만** 찾을 수 있게 됩니다 — 취소는 ⛔."
    )

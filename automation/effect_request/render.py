"""Approval cards for generic effect requests (owner envelope, append-only versions)."""

from __future__ import annotations

import shlex
from typing import Final

from .model import DRIVE_SAVE, EffectRequest

RENDER_VERSION: Final = "effect-request-v1"
MAX_MESSAGE_CHARS: Final = 1900
_COMMAND_LIMIT: Final = 700


class RenderError(ValueError):
    pass


def drive_location(payload: dict[str, object]) -> str:
    parts = [str(part) for part in payload.get("folder_parts", ())]
    return "/".join((*parts, str(payload.get("drive_name", ""))))


def _size(size: object) -> str:
    value = int(size) if isinstance(size, int) else 0
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def _drive_fact(record: EffectRequest) -> tuple[str, str, str, str]:
    payload = record.payload
    fact = (
        f"할 일: {record.summary}\n"
        f"파일 이름: {payload.get('drive_name')}\n"
        f"Drive 위치: {drive_location(payload)}\n"
        f"공유 범위: 비공개(형 계정만 볼 수 있음) — 다른 사람에게 보이지 않는다\n"
        f"크기: {_size(payload.get('size'))} · sha256: {payload.get('sha256')}\n"
        "같은 이름 파일이 그 폴더에 이미 있으면 그 파일을 새 내용으로 갱신한다"
    )
    return (
        "Drive 저장 승인 요청", fact, "✅ 저장 / ⛔ 취소",
        "✅ 뒤 이 파일을 이 위치에 비공개로 올리고, 다시 내려받아 sha256 이 같은지 확인한 뒤 링크를 남긴다",
    )


def _command_fact(record: EffectRequest) -> tuple[str, str, str, str]:
    payload = record.payload
    command = shlex.join(tuple(str(item) for item in payload.get("argv", ())))
    if len(command) > _COMMAND_LIMIT:
        command = command[:_COMMAND_LIMIT] + f" …(전체 {len(command)}자, 해시에 전부 묶임)"
    fact = (
        f"할 일: {record.summary}\n"
        f"영향 대상: {payload.get('target')}\n"
        f"실행 명령(작업 폴더 {payload.get('cwd')}):\n```\n{command}\n```\n"
        f"제한 시간: {payload.get('timeout')}초 · 한 번만 실행하고 실패해도 다시 실행하지 않는다"
    )
    return (
        "외부 작업 승인 요청", fact, "✅ 실행 / ⛔ 취소",
        "✅ 뒤 이 명령을 그대로 한 번 실행하고 종료 코드와 출력 끝부분(가린 것)을 이 스레드에 남긴다",
    )


def render_effect_approval(record: EffectRequest, *, render_version: str = RENDER_VERSION) -> str:
    if render_version != RENDER_VERSION:
        raise RenderError(f"unsupported effect render version: {render_version}")
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise RenderError("owner envelope unavailable") from error
    subject, fact, verbs, next_step = (
        _drive_fact(record) if record.effect == DRIVE_SAVE else _command_fact(record)
    )
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=record.request_id,
        subject=subject,
        fact=f"{fact}\n판본: {RENDER_VERSION}",
        location=here,
        owner=om.Action("react", here, verbs),
        agent_next=next_step,
        recovery="not_applicable",
        detail=om.Approval(None, "아무것도 실행하지 않는다"),
        render_version="owner-ko-v2",
    )
    try:
        lines = om.render(message, destination=here).splitlines()
    except om.OwnerMessageError as error:
        raise RenderError("owner envelope cannot render") from error
    content = "\n".join((*lines[:-1], f"- action_hash: `{record.action_hash}`", lines[-1]))
    if len(content) > MAX_MESSAGE_CHARS:
        raise RenderError("effect approval exceeds the postable length")
    return content

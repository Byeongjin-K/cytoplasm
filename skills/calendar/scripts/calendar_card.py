"""Prepare calendar card bytes before resolving a request's Discord surface."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import calendar_confirm
import calendar_core
import calendar_gate

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import JsonValue
from calendar_confirm_input import DraftRecord


def prepare_draft(draft: DraftRecord) -> DraftRecord:
    from automation.interop.approval_card import CardRenderError, prepare

    version = draft.get("render_version", "1" if draft.get("approval_thread_id") else "4")
    try:
        if "render_version" not in draft and not draft.get("approval_thread_id"):
            if _changes(draft).color is not None:
                version = "5"
        card = prepare(lambda selected: calendar_confirm.render_confirmation(
            {**draft, "render_version": selected},
        ), version)
    except CardRenderError as error:
        raise calendar_gate.GateError(str(error), 3) from error
    return {**draft, "render_version": card.render_version, "approval_content": card.content}


def render_envelope(draft: DraftRecord) -> str:
    """Versioned card presentation; calendar_confirm retains the public call seam."""
    from automation.interop.approval_card import CardRenderError
    from automation.interop import owner_message
    from calendar_binding import reaction_instruction

    version = draft.get("render_version")
    if version not in {"2", "3", "4", "5"}:
        raise CardRenderError("unknown calendar card render version")
    if not callable(getattr(owner_message, "render", None)):
        raise CardRenderError("owner envelope unavailable")
    here = owner_message.Ref(scope="self")
    envelope = owner_message.OwnerMessage(
        subject_key=str(draft["id"]), subject="캘린더 변경", fact=change_details(draft),
        location=here, owner=owner_message.Action("react", here, reaction_instruction()),
        agent_next="승인된 캘린더 변경만 실행", recovery="not_applicable",
        detail=owner_message.Approval(None, "캘린더를 변경하지 않음"),
        render_version="owner-ko-v2" if version in {"4", "5"} else "owner-ko-v1",
    )
    try:
        body = owner_message.render(envelope, destination=here)
    except owner_message.OwnerMessageError as error:
        raise CardRenderError("calendar envelope cannot render") from error
    if version in {"4", "5"}:
        return f"{body}\n해시: `sha256:{draft['sha256']}`"
    return f"{body}\nsha256:{draft['sha256']}"


class CalendarAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class ChangeFields:
    action: CalendarAction
    summary: str | None
    start: str | None
    end: str | None
    color: str | None = None


def _time_value(value: JsonValue) -> str | None:
    from automation.interop.approval_card import CardRenderError

    try:
        match value:
            case None:
                return None
            case {"dateTime": str(text)}:
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    zone = value.get("timeZone")
                    if not isinstance(zone, str):
                        raise CardRenderError("calendar timezone is unavailable")
                    parsed = parsed.replace(tzinfo=ZoneInfo(zone))
                return parsed.astimezone(calendar_core.KST).isoformat()
            case {"date": str(text)}:
                return date.fromisoformat(text).isoformat()
            case _:
                raise CardRenderError("calendar time field is invalid")
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise CardRenderError("calendar time field is invalid") from error


def _changes(draft: DraftRecord) -> ChangeFields:
    """Read only fields actually carried by the frozen Calendar command."""
    from automation.interop.approval_card import CardRenderError

    try:
        argv = draft.get("argv")
        if argv is None:
            raise CardRenderError("calendar frozen command is missing")
        if calendar_core.draft_sha256(draft) != draft["sha256"]:
            raise CardRenderError("calendar draft content hash mismatch")
        action = CalendarAction(draft["action"])
        operation = {CalendarAction.CREATE: "insert", CalendarAction.UPDATE: "patch", CalendarAction.DELETE: "delete"}[action]
        if argv[:4] != ["gws", "calendar", "events", operation]:
            raise CardRenderError("calendar command does not match draft action")
        params: JsonValue = json.loads(argv[argv.index("--params") + 1])
        if not isinstance(params, dict) or params.get("calendarId") != draft["calendar_id"]:
            raise CardRenderError("calendar command target does not match draft")
        if draft["event_id"] and params.get("eventId") != draft["event_id"]:
            raise CardRenderError("calendar event target does not match draft")
        match action:
            case CalendarAction.DELETE:
                return ChangeFields(action, None, None, None)
            case CalendarAction.CREATE | CalendarAction.UPDATE:
                pass
            case unreachable:
                assert_never(unreachable)
        body: JsonValue = json.loads(argv[argv.index("--json") + 1])
        if not isinstance(body, dict) or not body or body.keys() - {"summary", "start", "end", "colorId"} or None in body.values():
            raise CardRenderError("calendar command fields cannot be displayed")
        color = body.get("colorId")
        if color is not None:
            from automation.calendar_default_color import COLORS

            if action != CalendarAction.CREATE or not isinstance(color, str) or color not in COLORS:
                raise CardRenderError("calendar color field is invalid")
        summary = body.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise CardRenderError("calendar title field is invalid")
        return ChangeFields(action, summary, _time_value(body.get("start")), _time_value(body.get("end")), color)
    except (KeyError, ValueError, IndexError) as error:
        raise CardRenderError("calendar frozen command is invalid") from error


def capture_context(draft: DraftRecord) -> DraftRecord:
    """Freeze the new update/delete draft's display context before it can be posted.

    Existing hash-bound fields carry the resulting title/interval; argv alone says
    which fields change. No before-values or metadata are appended to old approvals.
    """
    import calendar_preflight
    from automation.interop.approval_card import CardRenderError

    try:
        event = calendar_preflight.read_event(draft["calendar_id"], draft["event_id"])
        if event is None or event.get("id") != draft["event_id"]:
            raise CardRenderError("calendar event context is unavailable")
        title = event.get("summary", "")
        start, end = _time_value(event.get("start")), _time_value(event.get("end"))
        if not isinstance(title, str) or start is None or end is None:
            raise CardRenderError("calendar event context is incomplete")
        changed = _changes(draft)
        captured: DraftRecord = {
            **draft, "summary": title if changed.summary is None else changed.summary,
            "start": start if changed.start is None else changed.start,
            "end": end if changed.end is None else changed.end,
        }
        captured["sha256"] = calendar_core.draft_sha256(captured)
    except CardRenderError as error:
        raise calendar_gate.GateError(str(error), 3) from error
    calendar_gate.persist_draft(captured)
    return captured


def change_details(draft: DraftRecord) -> str:
    """Render append-only calendar facts while preserving v2/v3 bytes."""
    match draft.get("render_version"):
        case "2":
            return str(draft["action"])
        case "3":
            changed = _changes(draft)
            multiline = False
        case "4" | "5":
            changed = _changes(draft)
            multiline = True
        case _:
            from automation.interop.approval_card import CardRenderError
            raise CardRenderError("unknown calendar detail version")
    values = (
        ("제목", draft["summary"] if changed.summary is None else changed.summary, changed.summary),
        ("시작", draft["start"] if changed.start is None else changed.start, changed.start),
        ("종료", draft["end"] if changed.end is None else changed.end, changed.end),
    )
    markers = ("", "")
    match changed.action:
        case CalendarAction.CREATE:
            action = "생성: 아래 일정 추가"
        case CalendarAction.UPDATE:
            action = "수정: 지정한 항목만 변경 (변경 전 값은 저장하지 않음)"
            markers = (" (유지·초안 조회값)", " (변경)")
        case CalendarAction.DELETE:
            action = "삭제: 아래 일정 제거 (저장된 초안 기준)"
        case unreachable:
            assert_never(unreachable)
    lines = [action]
    for label, value, replacement in values:
        suffix = markers[replacement is not None]
        shown = value or "(저장된 값 없음)"
        if multiline and label in {"시작", "종료"} and value:
            shown = _display_time(value)
        lines.append(f"{label}: {shown}{suffix}")
    if changed.color is not None:
        from automation.calendar_default_color import color_label
        from automation.interop.approval_card import CardRenderError

        if draft.get("render_version") != "5":
            raise CardRenderError("calendar color requires card version 5")
        lines.append(f"색: {color_label(changed.color)}")
    if not multiline:
        return " · ".join(lines)
    return "\n".join((f"작업: {lines[0]}", *lines[1:]))


def _display_time(value: str) -> str:
    """Show timed values in KST while leaving Calendar all-day dates unchanged."""
    from automation.interop.approval_card import CardRenderError

    try:
        if "T" not in value:
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise CardRenderError("calendar display timezone is unavailable")
        local = parsed.astimezone(calendar_core.KST)
        offset = local.strftime("%z")
        return f"{local:%Y-%m-%d %H:%M} ({offset[:3]}:{offset[3:]})"
    except ValueError as error:
        raise CardRenderError("calendar display time is invalid") from error


def draft_created(value: str) -> datetime:
    created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise calendar_gate.GateError("드래프트 created UTC 누락", 3)
    return created.astimezone(UTC)

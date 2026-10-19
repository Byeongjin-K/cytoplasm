"""Pure logic for the reminder poller (W3-2).

Window check (55-65 min before an event start), idempotency keys, milestone
D-3/D-1/D-day offsets, KST/TZ handling, message composition, and a
dependency-free milestones.yaml subset parser (the exact shape
skills/meeting/scripts/meeting_actions._emit_milestones writes — the gateway
venv has no PyYAML, per the W2-3 learning).

No I/O, no subprocess, no network — everything here is pytest-able.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage

KST = timezone(timedelta(hours=9), "KST")
WINDOW_MIN = timedelta(minutes=55)
WINDOW_MAX = timedelta(minutes=65)
MILESTONE_OFFSETS: dict[int, str] = {3: "D-3", 1: "D-1", 0: "D-day"}

_MILESTONE_ITEM = re.compile(r"^  - title: (.+)$")
_MILESTONE_FIELD = re.compile(r"^    (deadline|basis|source|added): (.+)$")
_DEADLINE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{6,}"), "[MASKED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b(?:Bearer|Bot)\s+\S+", re.IGNORECASE), "[MASKED_AUTH]"),
    (re.compile(r"\b[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b\d{17,19}\b"), "[MASKED_ID]"),
)


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """One timed calendar event (all-day events are filtered out upstream)."""

    event_id: str
    start: datetime
    summary: str
    source_url: str | None = None

    @property
    def start_iso(self) -> str:
        return self.start.isoformat()


def parse_events(payload: str) -> list[CalendarEvent]:
    """Parse `gws calendar events list` stdout into timed events.

    All-day events (start.date without start.dateTime) carry no start time and
    are skipped — a 55-65 min pre-start reminder is undefined for them.
    """
    parsed: object = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("gws events list output is not a JSON object")
    events: list[CalendarEvent] = []
    items = parsed.get("items", [])
    if not isinstance(items, list):
        raise ValueError("gws events list items is not a list")
    for item in items:
        if not isinstance(item, dict):
            continue
        start_raw = item.get("start", {})
        date_time = start_raw.get("dateTime") if isinstance(start_raw, dict) else None
        if not isinstance(date_time, str):
            continue
        events.append(
            CalendarEvent(
                event_id=str(item.get("id", "")),
                start=datetime.fromisoformat(date_time),
                summary=str(item.get("summary", "")),
                source_url=link if isinstance(link := item.get("htmlLink"), str) else None,
            )
        )
    return events


def in_reminder_window(start: datetime, now: datetime) -> bool:
    """True when the event starts 55-65 minutes from now (inclusive bounds).

    The 10-minute window with a 5-minute poll guarantees at least one hit per
    event; double hits are collapsed by the idempotency store.
    """
    delta = start - now
    return WINDOW_MIN <= delta <= WINDOW_MAX


def event_key(event: CalendarEvent) -> str:
    """Idempotency key: a given (event_id, start_iso) is reminded ONCE."""
    return f"{event.event_id}|{event.start_iso}"


def parse_milestones(raw: str) -> list[dict[str, str]]:
    """Dependency-free parser for the W2-3 milestones.yaml emit format."""
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in raw.splitlines():
        item = _MILESTONE_ITEM.match(line)
        if item:
            current = {"title": _scalar(item.group(1))}
            entries.append(current)
            continue
        if current is None:
            continue
        field = _MILESTONE_FIELD.match(line)
        if field:
            current[field.group(1)] = _scalar(field.group(2))
    return entries


def _scalar(token: str) -> str:
    token = token.strip()
    if token.startswith('"'):
        loaded: object = json.loads(token)
        return str(loaded)
    return token


def milestone_offset(deadline: str, today: date) -> str | None:
    """Return "D-3"/"D-1"/"D-day" when today matches, else None.

    Non-date deadlines (e.g. "미정") and past deadlines yield None.
    """
    matched = _DEADLINE.match(deadline.strip())
    if not matched:
        return None
    due = date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
    return MILESTONE_OFFSETS.get((due - today).days)


def milestone_key(entry: dict[str, str], offset: str) -> str:
    """Idempotency key: one reminder per milestone identity per offset."""
    return f"{entry.get('title', '')}|{entry.get('deadline', '')}|{offset}"


@dataclass(frozen=True, slots=True)
class ReminderContext:
    """멱등 키와 원본 위치. 일정 조회 구간이 있을 때만 관측 구간을 담는다."""

    key: str
    source: tuple[str, str]
    url: str | None = None
    window: tuple[datetime, datetime] | None = None


def reminder_message(content: str, context: ReminderContext | None) -> OwnerMessage | None:
    """플랫 런타임도 import 가능하며, 봉투 부재는 기존 문자열로 배달한다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Periodic, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    if context is None:
        return None
    detail = Result(outcome="executed") if context.window is None else Periodic(*context.window)
    return OwnerMessage(
        subject_key=context.key, subject="리마인더", fact=content,
        location=Ref(scope="resource", url=context.url, search=context.source),
        owner=Action(verb="none"), agent_next="다음 틱에 새 대상 확인",
        recovery="not_applicable", detail=detail, render_version="owner-ko-v5",
    )


def compose_event_reminder(event: CalendarEvent, now: datetime) -> str:
    start_kst = event.start.astimezone(KST)
    minutes = round((event.start - now).total_seconds() / 60)
    return (
        f"⏰ 일정 리마인더: 약 {minutes}분 후 「{event.summary}」 시작 — "
        f"{start_kst.strftime('%Y-%m-%d %H:%M')} KST"
    )


def compose_milestone_reminder(entry: dict[str, str], offset: str) -> str:
    return (
        f"📌 마일스톤 {offset}: {entry.get('title', '?')} "
        f"(마감 {entry.get('deadline', '?')})"
    )

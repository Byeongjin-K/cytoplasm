"""Pure logic for the gws-calendar skill (W3-1): KST time parsing, change
summary rendering, gws argv building, and gate-parity action hashing.

No I/O, no subprocess, no network — everything here is pytest-able.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import JsonValue
    from calendar_confirm_input import DraftRecord
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")
DEFAULT_DURATION_MIN = 60
EXTERNAL_EFFECT_TARGET_ID = "tool:gws_calendar_mutation:gws"
_WEEKDAY_KO = "월화수목금토일"
_DAY_WORDS = {"오늘": 0, "내일": 1, "모레": 2, "글피": 3}
_WEEK_WORDS = {"이번": 0, "다음": 1, "다다음": 2}
_AMBIGUOUS_MARKERS = ("쯤", "정도", "언젠가", "조만간", "나중에", "다음에", "아무 때", "아무때")
_TRAILING_VERBS = re.compile(
    r"\s*(?:일정|약속)?\s*(?:으로|로)?\s*"
    r"(?:잡아\s*줘|등록해\s*줘|추가해\s*줘|만들어\s*줘|넣어\s*줘|잡아|등록|추가|해\s*줘)?[.!?\s]*$"
)
_DATE_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DATE_MD = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")
_DATE_WORD = re.compile("|".join(_DAY_WORDS))
_DATE_WEEK = re.compile(r"(이번|다음|다다음)\s*주(?:\s*([월화수목금토일])요일)?")
_DATE_OFFSET = re.compile(r"(\d{1,2})일\s*(?:뒤|후)")
_TIME = re.compile(
    r"(?:(오전|오후)\s*)?(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분|\s*(반))?"
    r"|(\d{1,2}):(\d{2})|(정오)|(자정)"
)
_DURATION = re.compile(r"(\d{1,3})\s*시간(\s*반)?|(\d{1,3})\s*분(?:\s*(?:동안|간))?")


class ParseRejected(ValueError):
    """Input that must be rejected outright (exit 2) — bad or past request."""


class AmbiguousTime(ValueError):
    """Ambiguous date/time (exit 5): carry the exact re-ask question for cha."""

    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


@dataclass(frozen=True, slots=True)
class ParsedRequest:
    """A natural-language request resolved to a concrete KST interval."""

    summary: str
    start: datetime
    end: datetime


Span = tuple[int, int]


def parse_request(text: str, now: datetime) -> ParsedRequest:
    """Parse Korean natural language into a concrete event; never guess."""
    body = text.strip()
    for marker in _AMBIGUOUS_MARKERS:
        if marker in body:
            raise AmbiguousTime(
                f"'{marker}' 같은 표현으로는 시각을 정할 수 없어요. "
                "정확한 날짜와 시각(예: 내일 오후 3시)을 알려주세요."
            )
    spans: list[Span] = []
    day = _extract_date(body, now.astimezone(KST).date(), spans)
    times = _extract_times(body, spans)
    if not times:
        raise AmbiguousTime("몇 시로 잡을까요? 시각(예: 오후 3시, 15:00)을 알려주세요.")
    duration = _extract_duration(body, spans)
    start = datetime.combine(day, times[0], KST)
    if len(times) >= 2:
        end = datetime.combine(day, times[1], KST)
        if end <= start:
            raise ParseRejected("종료 시각이 시작 시각보다 빠릅니다")
    else:
        end = start + timedelta(minutes=duration or DEFAULT_DURATION_MIN)
    if start <= now.astimezone(KST):
        raise ParseRejected(f"과거 시각입니다: {start.isoformat()}")
    return ParsedRequest(summary=_strip_spans(body, spans), start=start, end=end)


def resolve_relative_day(text: str, now: datetime) -> date:
    """Resolve the date expression in ``text`` relative to ``now`` in KST."""
    today = now.astimezone(KST).date()
    match = _DATE_ISO.search(text)
    if match:
        return date(int(match[1]), int(match[2]), int(match[3]))
    match = _DATE_MD.search(text)
    if match:
        candidate = date(today.year, int(match[1]), int(match[2]))
        return candidate.replace(year=today.year + 1) if candidate < today else candidate
    match = _DATE_WORD.search(text)
    if match:
        return today + timedelta(days=_DAY_WORDS[match[0]])
    match = _DATE_WEEK.search(text)
    if match:
        if match[2] is None:
            raise AmbiguousTime(
                f"'{match[1]}주' 중 언제인가요? 요일까지(예: {match[1]}주 화요일) 알려주세요."
            )
        monday = today - timedelta(days=today.weekday())
        target = monday + timedelta(weeks=_WEEK_WORDS[match[1]], days=_WEEKDAY_KO.index(match[2]))
        if target < today:
            raise ParseRejected(f"이미 지난 날짜입니다: {target.isoformat()}")
        return target
    match = _DATE_OFFSET.search(text)
    if match:
        return today + timedelta(days=int(match[1]))
    raise AmbiguousTime("어느 날짜인가요? 날짜(예: 내일, 7월 20일)를 알려주세요.")


def _extract_date(text: str, today: date, spans: list[Span]) -> date:
    span = _date_span(text)
    if span is not None:
        spans.append(span)
    return resolve_relative_day(text, datetime.combine(today, time.min, KST))


def _date_span(text: str) -> Span | None:
    for pattern in (_DATE_ISO, _DATE_MD, _DATE_WORD, _DATE_WEEK, _DATE_OFFSET):
        match = pattern.search(text)
        if match:
            return match.span()
    return None


def _extract_times(text: str, spans: list[Span]) -> list[time]:
    times: list[time] = []
    for match in _TIME.finditer(text):
        if any(match.start() < hi and match.end() > lo for lo, hi in spans):
            continue
        spans.append(match.span())
        times.append(_time_of(match))
        if len(times) == 2:
            break
    return times


def _time_of(match: re.Match[str]) -> time:
    if match[7]:
        return time(12, 0)
    if match[8]:
        return time(0, 0)
    if match[5]:
        hour, minute = int(match[5]), int(match[6])
    else:
        hour = int(match[2])
        minute = 30 if match[4] else int(match[3] or 0)
        if match[1] == "오후":
            hour = 12 if hour == 12 else hour + 12
        elif match[1] == "오전":
            hour = 0 if hour == 12 else hour
        elif 1 <= hour <= 12:
            raise AmbiguousTime(
                f"'{hour}시'가 오전인가요, 오후인가요? 오전/오후 또는 24시간제(예: {hour + 12}시)로 알려주세요."
            )
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ParseRejected(f"시각 범위를 벗어났습니다: {hour}시 {minute}분")
    return time(hour, minute)


def _extract_duration(text: str, spans: list[Span]) -> int | None:
    for match in _DURATION.finditer(text):
        if any(match.start() < hi and match.end() > lo for lo, hi in spans):
            continue
        spans.append(match.span())
        if match[1]:
            return int(match[1]) * 60 + (30 if match[2] else 0)
        return int(match[3])
    return None


def _strip_spans(text: str, spans: list[Span]) -> str:
    kept = "".join(
        ch for i, ch in enumerate(text) if not any(lo <= i < hi for lo, hi in spans)
    )
    kept = re.sub(r"\s+", " ", kept).strip()
    kept = re.sub(r"(?<!\S)(?:에|부터|까지)(?!\S)", "", kept)
    kept = re.sub(r"\s+", " ", kept).strip()
    kept = re.sub(r"^[\s,]*(?:에|부터|까지)\s+", "", kept)
    kept = _TRAILING_VERBS.sub("", kept)
    return kept.strip(" ,~")


def render_change_summary(
    *, action: str, summary: str, start: str, end: str, calendar_id: str, event_id: str
) -> str:
    """Render the Korean pre-confirmation change summary block."""
    labels = {"create": "생성", "update": "수정", "delete": "삭제"}
    when = "(변경 없음)"
    if start:
        begin = datetime.fromisoformat(start)
        finish = datetime.fromisoformat(end)
        day_ko = _WEEKDAY_KO[begin.weekday()]
        when = (
            f"{begin.strftime('%Y-%m-%d')} ({day_ko}) "
            f"{begin.strftime('%H:%M')} ~ {finish.strftime('%H:%M')} KST"
        )
    return (
        "CHANGE-SUMMARY\n"
        f"동작: {labels[action]}\n"
        f"제목: {summary or '(변경 없음)'}\n"
        f"일시: {when}\n"
        f"캘린더: {calendar_id}\n"
        f"대상 이벤트: {event_id or '(신규)'}"
    )


def _cjson(value: Mapping[str, str | Mapping[str, str]]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _event_time(value: datetime) -> dict[str, str]:
    return {"dateTime": value.isoformat(), "timeZone": "Asia/Seoul"}


def build_create_argv(calendar_id: str, request: ParsedRequest) -> tuple[str, ...]:
    body = {
        "summary": request.summary,
        "start": _event_time(request.start),
        "end": _event_time(request.end),
    }
    return (
        "gws", "calendar", "events", "insert",
        "--params", _cjson({"calendarId": calendar_id}),
        "--json", _cjson(body),
    )


def build_patch_argv(
    calendar_id: str, event_id: str, body: Mapping[str, str | Mapping[str, str]]
) -> tuple[str, ...]:
    return (
        "gws", "calendar", "events", "patch",
        "--params", _cjson({"calendarId": calendar_id, "eventId": event_id}),
        "--json", _cjson(body),
    )


def build_delete_argv(calendar_id: str, event_id: str) -> tuple[str, ...]:
    return (
        "gws", "calendar", "events", "delete",
        "--params", _cjson({"calendarId": calendar_id, "eventId": event_id}),
    )


def patch_body(request: ParsedRequest | None, summary: str) -> dict[str, str | dict[str, str]]:
    """Build a partial events.patch body from the requested changes."""
    body: dict[str, str | dict[str, str]] = {}
    if summary:
        body["summary"] = summary
    if request is not None:
        body["start"] = _event_time(request.start)
        body["end"] = _event_time(request.end)
    return body


def external_effect_action_hash(argv: tuple[str, ...]) -> str:
    """Hash-parity with automation.interop.effect_approval.action_hash.

    Canonical ToolCall: tool_name="gws", arguments={"command": shlex.join(argv)}
    — the same binding the deployed pre_tool_call gate computes for this call.
    """
    payload = {
        "action": "external_effect.tool_call",
        "arguments": {"command": shlex.join(argv)},
        "target_id": EXTERNAL_EFFECT_TARGET_ID,
        "tool_name": "gws",
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def draft_sha256(record: Mapping[str, JsonValue] | DraftRecord) -> str:
    """Content hash binding a draft to the exact mutation it will execute."""
    if "argv" not in record:
        raise KeyError("argv")
    bound = {key: record[key] for key in ("action", "argv", "calendar_id", "event_id", "summary", "start", "end")}
    canonical = json.dumps(bound, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

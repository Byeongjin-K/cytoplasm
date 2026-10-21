"""Same-event check for calendar creates (owner 2026-10-21, Q62).

소유자 지적: "해당 날짜 동일 시간에 유사한 일정이 있으면 검사해서 중복 생성하지 않아야지."
다이제스트의 `event_key` 는 제목 토큰 집합이 한 낱말만 달라도(예: "회의실") 다른 일정으로
보았다. 여기서는 실제 캘린더를 읽어 **같은 캘린더 · 같은 KST 날짜 · 겹치는 시간 ·
비슷한 제목**이면 같은 일정으로 본다.

- 제목 비교는 NFKC·casefold 뒤 글자·숫자만 남긴 문자열로 한다(괄호·띄어쓰기·문장부호 무시).
  같거나, 짧은 쪽이 4글자 이상이면서 긴 쪽에 들어 있거나, `difflib.SequenceMatcher`
  비율이 0.6 이상이면 비슷하다. 0.6 은 표준 라이브러리 `difflib.get_close_matches` 의
  기본 cutoff 다. 두 제목에 모두 숫자가 있고 어느 쪽도 다른 쪽 숫자를 다 담지 않으면
  (1차/2차, 301호/302호) 비슷해도 다른 일정이다.
- 시간은 반열린 구간 `[start, end)` 이 겹쳐야 한다 — 끝과 시작이 맞닿은 일정은 겹치지 않는다.
- 종일 일정·취소된 일정은 비교하지 않는다.

중복이면 먼저 있던 일정을 남기고 새 것은 만들지 않는다. 이 모듈은 판정과 조회, 그리고
"만들지 않았다"는 기록만 맡는다. 실행 뒤 경합 정리는 `calendar_after_insert` 가 한다.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import calendar_core
import calendar_gate

SIMILARITY: Final = 0.6
CONTAIN_MIN: Final = 4


class AlreadyExists(RuntimeError):
    """The approved create was not executed because the same event already exists."""

    def __init__(self, existing: Existing) -> None:
        super().__init__(existing.label())
        self.existing = existing


@dataclass(frozen=True, slots=True)
class Existing:
    """An event already on the calendar that a new create would duplicate."""

    event_id: str
    summary: str
    start: str
    end: str
    created: str = ""
    attendees: bool = False

    def record(self) -> dict[str, str]:
        return {"event_id": self.event_id, "summary": self.summary, "start": self.start, "end": self.end}

    def label(self) -> str:
        begin = datetime.fromisoformat(self.start).astimezone(calendar_core.KST)
        finish = datetime.fromisoformat(self.end).astimezone(calendar_core.KST)
        return f"{self.summary} · {begin:%Y-%m-%d %H:%M}~{finish:%H:%M}"


def _compact(title: str) -> str:
    return "".join(re.findall(r"[^\W_]", unicodedata.normalize("NFKC", title).casefold()))


def _numbers(title: str) -> set[str]:
    return {value.lstrip("0") or "0" for value in re.findall(r"\d+", unicodedata.normalize("NFKC", title))}


def similar_title(first: str, second: str) -> bool:
    left, right = _numbers(first), _numbers(second)
    if left and right and not (left <= right or right <= left):
        return False
    a, b = _compact(first), _compact(second)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= CONTAIN_MIN and short in long_:
        return True
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY


def _instant(value: object) -> datetime | None:
    """A timed Calendar boundary in KST; all-day dates and malformed values are None."""
    if not isinstance(value, Mapping) or not isinstance(value.get("dateTime"), str):
        return None
    try:
        parsed = datetime.fromisoformat(value["dateTime"])
        if parsed.tzinfo is None:
            zone = value.get("timeZone")
            if not isinstance(zone, str):
                return None
            parsed = parsed.replace(tzinfo=ZoneInfo(zone))
    except (ValueError, ZoneInfoNotFoundError):
        return None
    return parsed.astimezone(calendar_core.KST)


def duplicates(draft: Mapping[str, object], events: Sequence[Mapping[str, object]],
               *, exclude_id: str = "") -> list[Existing]:
    """Every timed, live event that is the same event as this create draft."""
    start = datetime.fromisoformat(str(draft["start"])).astimezone(calendar_core.KST)
    end = datetime.fromisoformat(str(draft["end"])).astimezone(calendar_core.KST)
    found: list[Existing] = []
    for event in events:
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id or event_id == exclude_id:
            continue
        if event.get("status") == "cancelled":
            continue
        begin, finish = _instant(event.get("start")), _instant(event.get("end"))
        title = event.get("summary")
        if begin is None or finish is None or not isinstance(title, str):
            continue
        if begin.date() != start.date() or not (begin < end and start < finish):
            continue
        if not similar_title(str(draft["summary"]), title):
            continue
        created = event.get("created")
        attendees = event.get("attendees")
        found.append(Existing(
            event_id=event_id, summary=title, start=begin.isoformat(), end=finish.isoformat(),
            created=created if isinstance(created, str) else "",
            attendees=isinstance(attendees, list) and bool(attendees),
        ))
    return found


def list_day_events(calendar_id: str, start: str) -> list[Mapping[str, object]]:
    """Read the draft's KST day from Google Calendar; a failed read refuses (fail-closed)."""
    day = datetime.fromisoformat(start).astimezone(calendar_core.KST).date()
    begin = datetime.combine(day, time.min, calendar_core.KST)
    params = {
        "calendarId": calendar_id, "maxResults": 250, "orderBy": "startTime", "singleEvents": True,
        "timeMin": begin.isoformat(), "timeMax": (begin + timedelta(days=1)).isoformat(),
    }
    result = subprocess.run(  # noqa: S603 -- read-only list with fixed argv
        [calendar_gate.gws_bin(), "calendar", "events", "list", "--params",
         json.dumps(params, ensure_ascii=False, separators=(",", ":"), sort_keys=True)],
        capture_output=True, text=True, timeout=calendar_gate.GWS_TIMEOUT_S, check=False,
        cwd=str(Path.home()),
    )
    if result.returncode != 0:
        raise calendar_gate.GateError("같은 날 일정 조회 실패 — 중복 확인 전에는 만들지 않습니다", 6)
    try:
        items = json.loads(result.stdout or "{}").get("items", [])
    except (json.JSONDecodeError, AttributeError) as error:
        raise calendar_gate.GateError("같은 날 일정 조회 결과를 읽을 수 없습니다", 6) from error
    if not isinstance(items, list):
        raise calendar_gate.GateError("같은 날 일정 조회 결과를 읽을 수 없습니다", 6)
    return [item for item in items if isinstance(item, Mapping)]


def find(draft: Mapping[str, object]) -> Existing | None:
    """The first existing event this create draft would duplicate, read live."""
    found = duplicates(draft, list_day_events(str(draft["calendar_id"]), str(draft["start"])))
    return found[0] if found else None


def flagged(draft: Mapping[str, object]) -> Existing | None:
    """The duplicate the approval card already showed (outside the content hash)."""
    value = draft.get("duplicate_of")
    if not isinstance(value, Mapping):
        return None
    fields = {key: value.get(key) for key in ("event_id", "summary", "start", "end")}
    if not all(isinstance(item, str) and item for item in fields.values()):
        return None
    return Existing(**{key: str(item) for key, item in fields.items()})


def annotate(draft: dict) -> dict:
    """Draft time: mark the card '이미 있음' when the event exists; never block a card on a failed read.

    The mark is outside ``draft_sha256`` and only narrows what ✅ does: a marked draft is
    never inserted. A failed read leaves the draft unmarked and the execution-time check
    (which does refuse on a failed read) still guards the insert.
    """
    if draft.get("action") != "create" or not draft.get("start") or not draft.get("end"):
        return draft
    try:
        existing = find(draft)
    except calendar_gate.GateError as error:
        print(f"DUPLICATE-CHECK-UNAVAILABLE draft={draft.get('id')} reason={error}", file=sys.stderr)
        return draft
    if existing is None:
        return draft
    print(f"DUPLICATE-FOUND draft={draft.get('id')} existing={existing.event_id}")
    return {**draft, "duplicate_of": existing.record()}


def before_insert(draft: Mapping[str, object]) -> Existing | None:
    """Execution time: the card's mark wins; otherwise read live (a failed read refuses)."""
    if draft.get("action") != "create" or not draft.get("start") or not draft.get("end"):
        return None
    return flagged(draft) or find(draft)


def record_skip(draft: Mapping[str, object], approval: calendar_gate.Approval, existing: Existing) -> None:
    """Seal the draft as not created, with an audit line, so the watcher answers '이미 있음'."""
    calendar_gate._append_record(
        calendar_gate._audit_record(dict(draft), approval, event_id=existing.event_id, status="skipped-duplicate"),
    )
    calendar_gate.write_json(calendar_gate._draft_path(str(draft["id"])), {
        **draft, "status": "duplicate", "duplicate_of": existing.record(),
        "approval_ref": approval.ref, "method": approval.method,
    })

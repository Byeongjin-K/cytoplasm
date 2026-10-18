"""Hermes cron 주기 → systemd `OnCalendar=` 한 줄(순수 변환).

선언의 `schedule=` 은 cron 다섯 칸 또는 `every <N>s|m|h`다(초 단위는 사용자 타이머 전용). 잡을 Hermes 에서 사용자
타이머로 옮겨도(`runner=systemd`) 주기 문자열은 그대로 두고 여기서 바꾼다 — 같은 행이 두 실행
주체 어느 쪽으로도 같은 시각을 뜻하게.

시각은 Hermes 설정의 시간대(`timezone:`)로 읽는다. Hermes 가 그 시간대로 cron 을 해석하므로
(노드 TZ 는 UTC) 타이머도 같은 시간대를 `OnCalendar` 끝에 붙인다.

바꿀 수 없는 것은 추측하지 않고 `ScheduleError` 다:
- 날짜(dom)와 요일(dow)을 함께 정한 cron — cron 은 둘 중 하나(OR), systemd 는 둘 다(AND)라 뜻이 갈린다.
- 60(분)·24(시)를 나누지 않는 `every` — Hermes 는 마지막 실행부터 세지만 달력 타이머는 시각에 맞춘다.
  나누어떨어질 때만 두 뜻이 같은 간격이 된다.
- 범위에 걸음(`1-10/2`), 요일 범위가 거꾸로인 것, 숫자 범위를 벗어난 값.
"""
from __future__ import annotations

import re
from typing import Final

_EVERY: Final = re.compile(r"every ([1-9][0-9]*)([smh])")
_NUMBER: Final = re.compile(r"[0-9]+")
_DAYS: Final = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
#: (최소, 최대, `*/N` 의 시작값) — 분·시·일·월.
_BOUNDS: Final = ((0, 59, 0), (0, 23, 0), (1, 31, 1), (1, 12, 1))
_TZ: Final = re.compile(r"[A-Za-z][A-Za-z0-9_+/-]*")


class ScheduleError(ValueError):
    """systemd 달력으로 같은 뜻을 낼 수 없는 주기."""


def _number(text: str, low: int, high: int) -> int:
    if not _NUMBER.fullmatch(text) or not low <= int(text) <= high:
        raise ScheduleError(f"out of range: {text}")
    return int(text)


def _field(text: str, bounds: tuple[int, int, int]) -> str:
    low, high, start = bounds
    if text == "*":
        return "*"
    out: list[str] = []
    for token in text.split(","):
        base, slash, step = token.partition("/")
        if slash:
            if base not in ("*", *(str(n) for n in range(low, high + 1))):
                raise ScheduleError(f"step on a range: {token}")
            first = start if base == "*" else _number(base, low, high)
            out.append(f"{first:02d}/{_number(step, 1, high + 1)}")
        elif "-" in token:
            a, _, b = token.partition("-")
            lo, hi = _number(a, low, high), _number(b, low, high)
            if lo > hi:
                raise ScheduleError(f"reversed range: {token}")
            out.append(f"{lo:02d}..{hi:02d}")
        else:
            out.append(f"{_number(token, low, high):02d}")
    return ",".join(out)


def _weekdays(text: str) -> str:
    if text == "*":
        return ""
    out: list[str] = []
    for token in text.split(","):
        if "/" in token:
            raise ScheduleError(f"step on weekdays: {token}")
        a, dash, b = token.partition("-")
        lo = _number(a, 0, 7)
        if not dash:
            out.append(_DAYS[lo])
            continue
        hi = _number(b, 0, 7)
        if lo > hi:
            raise ScheduleError(f"reversed weekday range: {token}")
        out.append(f"{_DAYS[lo]}..{_DAYS[hi]}")
    return ",".join(out)


def on_calendar(schedule: str, timezone: str = "") -> str:
    """`schedule`(Hermes 문법) → `OnCalendar=` 값. 시간대가 있으면 끝에 붙인다."""
    if timezone and not _TZ.fullmatch(timezone):
        raise ScheduleError(f"bad timezone: {timezone[:40]}")
    suffix = f" {timezone}" if timezone else ""
    text = " ".join(schedule.split())
    if every := _EVERY.fullmatch(text):
        n, unit = int(every[1]), every[2]
        whole = 24 if unit == "h" else 60
        if whole % n:
            raise ScheduleError(f"interval does not divide {'an hour' if unit == 'm' else 'a day'}: {text}")
        if unit == "s":
            return f"*-*-* *:*:00/{n}{suffix}" if n < 60 else f"*-*-* *:*:00{suffix}"
        if unit == "m":
            return f"*-*-* *:00/{n}:00{suffix}" if n < 60 else f"*-*-* *:00:00{suffix}"
        return f"*-*-* 00/{n}:00:00{suffix}" if n < 24 else f"*-*-* 00:00:00{suffix}"
    fields = text.split(" ")
    if len(fields) != 5:
        raise ScheduleError(f"not five cron fields: {text[:60]}")
    minute, hour, dom, month, dow = fields
    if dom != "*" and dow != "*":
        raise ScheduleError("both day-of-month and weekday are set (cron OR vs systemd AND)")
    days = _weekdays(dow)
    date = f"*-{_field(month, _BOUNDS[3])}-{_field(dom, _BOUNDS[2])}"
    clock = f"{_field(hour, _BOUNDS[1])}:{_field(minute, _BOUNDS[0])}:00"
    return f"{days + ' ' if days else ''}{date} {clock}{suffix}"

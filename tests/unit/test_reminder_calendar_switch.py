"""The node's calendar reminders are off unless switched on; milestone D-day reminders always run.

Owner Q276, 2026-10-19: "유돌 것만 남기고 노드 일정 리마인더는 끄기 — 마일스톤 D-day 알림은 노드 그대로".
YooDori announces upcoming events now, so the node stays quiet about them unless
~/.hermes/reminder-poller/calendar-events holds exactly "on". New file so the recorded reminder
cases keep their shape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from automation.reminder_poller import poll_reminders


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("REMINDER_CALENDAR_EVENTS", raising=False)
    return tmp_path


def _flag(home: Path, value: bytes) -> None:
    path = home / ".hermes/reminder-poller/calendar-events"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def test_calendar_reminders_are_off_by_default(home: Path) -> None:
    assert poll_reminders.calendar_events_on() is False


@pytest.mark.parametrize(("value", "on"), [(b"on", True), (b"on\n", True), (b"off", False), (b"yes", False)])
def test_the_switch_file_turns_them_on_only_with_on(home: Path, value: bytes, on: bool) -> None:
    _flag(home, value)
    assert poll_reminders.calendar_events_on() is on


def test_off_sends_no_event_but_still_sends_milestones(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(poll_reminders, "fetch_events", lambda now: pytest.fail("calendar read while off"))
    monkeypatch.setattr(poll_reminders, "read_milestones", lambda: [{"title": "논문 제출", "deadline": "x"}])
    monkeypatch.setattr(poll_reminders.poller_core, "milestone_offset", lambda deadline, today: "D-day")
    monkeypatch.setattr(poll_reminders, "_deliver", lambda db, sender, kind, key, body: sent.append(kind))
    assert poll_reminders.main() == 0
    assert sent == ["milestone"]

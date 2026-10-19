"""W3-2 reminder poller — window / idempotency / milestone offsets / TZ."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from automation.reminder_poller import poll_reminders, poller_core, reminder_store

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=poller_core.KST)


def _event(minutes_out: int, event_id: str = "evt1", summary: str = "테스트 미팅") -> poller_core.CalendarEvent:
    return poller_core.CalendarEvent(
        event_id=event_id, start=NOW + timedelta(minutes=minutes_out), summary=summary
    )


# --- 55-65 min window ---------------------------------------------------------

@pytest.mark.parametrize(
    ("minutes_out", "expected"),
    [(54, False), (55, True), (60, True), (65, True), (66, False), (0, False), (-60, False)],
)
def test_reminder_window_bounds(minutes_out: int, expected: bool) -> None:
    assert poller_core.in_reminder_window(NOW + timedelta(minutes=minutes_out), NOW) is expected


def test_window_is_tz_aware_across_utc_kst() -> None:
    """The same instant expressed in UTC must land in the same window."""
    start_utc = (NOW + timedelta(minutes=60)).astimezone(UTC)
    assert poller_core.in_reminder_window(start_utc, NOW)


# --- gws events list parsing ---------------------------------------------------

def test_parse_events_skips_all_day_and_reads_datetime() -> None:
    payload = json.dumps({
        "items": [
            {"id": "abc123", "summary": "실험 미팅",
             "start": {"dateTime": "2026-07-15T13:00:00+09:00"}},
            {"id": "allday", "summary": "휴가", "start": {"date": "2026-07-15"}},
        ]
    })
    events = poller_core.parse_events(payload)
    assert len(events) == 1
    assert events[0].event_id == "abc123"
    assert events[0].start_iso == "2026-07-15T13:00:00+09:00"


def test_parse_events_empty_items() -> None:
    assert poller_core.parse_events("{}") == []


# --- idempotency store ----------------------------------------------------------

def test_same_event_polled_twice_sends_once(tmp_path: Path) -> None:
    db = tmp_path / "reminders.db"
    key = poller_core.event_key(_event(60))
    assert reminder_store.claim(db, "event", key) is True
    assert reminder_store.claim(db, "event", key) is False


def test_distinct_start_iso_is_a_new_reminder(tmp_path: Path) -> None:
    """(event_id, start_iso) key: a rescheduled event reminds again."""
    db = tmp_path / "reminders.db"
    assert reminder_store.claim(db, "event", poller_core.event_key(_event(60))) is True
    moved = _event(120)
    assert reminder_store.claim(db, "event", poller_core.event_key(moved)) is True


def test_concurrent_claims_have_exactly_one_winner(tmp_path: Path) -> None:
    db = tmp_path / "reminders.db"
    key = poller_core.event_key(_event(60))
    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = list(pool.map(lambda _: reminder_store.claim(db, "event", key), range(8)))
    assert winners.count(True) == 1


def test_failed_send_releases_claim_for_retry(tmp_path: Path) -> None:
    db = tmp_path / "reminders.db"

    class FailingSender:
        def send(self, body: str) -> None:
            raise RuntimeError("discord down")

    with pytest.raises(RuntimeError):
        poll_reminders._deliver(db, FailingSender(), "event", "k1", "body")  # type: ignore[arg-type]
    assert reminder_store.claim(db, "event", "k1") is True


# --- milestone offsets -----------------------------------------------------------

TODAY = date(2026, 7, 15)


@pytest.mark.parametrize(
    ("deadline", "expected"),
    [
        ("2026-07-18", "D-3"),
        ("2026-07-16", "D-1"),
        ("2026-07-15", "D-day"),
        ("2026-07-17", None),   # D-2 is not a reminder offset
        ("2026-07-14", None),   # past deadline
        ("2026-08-01", None),   # too far out
        ("미정", None),
        ("not-a-date", None),
    ],
)
def test_milestone_offsets(deadline: str, expected: str | None) -> None:
    assert poller_core.milestone_offset(deadline, TODAY) == expected


def test_milestone_key_is_per_offset(tmp_path: Path) -> None:
    """Idempotent per milestone+offset: D-3 and D-1 both fire, each once."""
    db = tmp_path / "reminders.db"
    entry = {"title": "논문 제출", "deadline": "2026-07-18"}
    d3 = poller_core.milestone_key(entry, "D-3")
    d1 = poller_core.milestone_key(entry, "D-1")
    assert d3 != d1
    assert reminder_store.claim(db, "milestone", d3) is True
    assert reminder_store.claim(db, "milestone", d3) is False
    assert reminder_store.claim(db, "milestone", d1) is True


def test_parse_milestones_w23_emit_format() -> None:
    raw = (
        "# Managed by the meeting skill (W2-3). Consumed by W3 reminders.\n"
        "milestones:\n"
        '  - title: "[민감] 회의 마일스톤 1 — 상세: ~/notes/meetings/x.md"\n'
        '    deadline: "2026-07-16"\n'
        '    basis: "로컬 노트 참조"\n'
        '    source: "meeting:x.md"\n'
        '    added: "2026-07-15T20:58:40+09:00"\n'
        '  - title: "미정 마일스톤"\n'
        '    deadline: "미정"\n'
    )
    entries = poller_core.parse_milestones(raw)
    assert len(entries) == 2
    assert entries[0]["deadline"] == "2026-07-16"
    assert entries[0]["title"].startswith("[민감] 회의 마일스톤 1")
    assert entries[1]["deadline"] == "미정"


# --- full poll loop (dry-run, deterministic now) ---------------------------------

def _run_poll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> list[str]:
    events = {
        "items": [
            {"id": "seed01", "summary": "E2E 시드",
             "start": {"dateTime": (NOW + timedelta(minutes=60)).isoformat()}},
            {"id": "later", "summary": "다음 일정",
             "start": {"dateTime": (NOW + timedelta(minutes=80)).isoformat()}},
        ]
    }
    events_file = tmp_path / "events.json"
    events_file.write_text(json.dumps(events), encoding="utf-8")
    milestones_file = tmp_path / "milestones.yaml"
    milestones_file.write_text(
        'milestones:\n  - title: "D-1 픽스처"\n    deadline: "2026-07-16"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMINDER_EVENTS_FILE", str(events_file))
    monkeypatch.setenv("REMINDER_MILESTONES_FILE", str(milestones_file))
    monkeypatch.setenv("REMINDER_DB", str(tmp_path / "reminders.db"))
    monkeypatch.setenv("REMINDER_NOW", NOW.isoformat())
    monkeypatch.setenv("REMINDER_CALENDAR_EVENTS", "1")  # the node calendar leg is off by default (Q276)
    monkeypatch.setenv("REMINDER_DRY_RUN", "1")
    assert poll_reminders.main() == 0
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("DRY-RUN")]


def test_poll_loop_sends_once_then_never_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _run_poll(tmp_path, monkeypatch, capsys)
    assert len(first) == 2  # one in-window event + one D-1 milestone
    assert any("E2E 시드" in line for line in first)
    assert any("D-1" in line and "픽스처" in line for line in first)
    assert not any("다음 일정" in line for line in first)  # 80 min out: not in window
    second = _run_poll(tmp_path, monkeypatch, capsys)
    assert second == []  # idempotent re-poll: zero sends


# --- composition ------------------------------------------------------------------

def test_event_reminder_body_kst() -> None:
    body = poller_core.compose_event_reminder(_event(60, summary="실험 미팅"), NOW)
    assert "실험 미팅" in body
    assert "2026-07-15 13:00" in body
    assert "60분" in body


def test_milestone_reminder_body() -> None:
    body = poller_core.compose_milestone_reminder(
        {"title": "논문 제출", "deadline": "2026-07-16"}, "D-1"
    )
    assert "D-1" in body and "논문 제출" in body and "2026-07-16" in body

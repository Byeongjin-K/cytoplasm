"""A calendar or milestone reminder reaches the owner as one line, not the five-field envelope.

2026-10-19 12:25 KST the owner's channel showed '대상: 리마인더 (<event id>|…) 사실: ⏰ …' for a
13:30 meeting. The renderer owner-ko-v5 prints only the fact; the key, source and the rest of the
envelope stay with the sender. Earlier render versions are unchanged. New file so the recorded
reminder and owner-message cases keep their shape.
"""
from __future__ import annotations

from datetime import UTC, datetime

from automation.interop import owner_message as om
from automation.reminder_poller import poller_core

FACT = "⏰ 일정 리마인더: 약 30분 후 「김천 회의」 시작 — 2026-10-19 13:30 KST"


def test_a_reminder_envelope_renders_as_its_fact_alone() -> None:
    window = (datetime(2026, 10, 19, 3, 0, tzinfo=UTC), datetime(2026, 10, 19, 5, 0, tzinfo=UTC))
    context = poller_core.ReminderContext(
        key="evt|2026-10-19T13:30:00+09:00", source=("calendar", "primary"),
        url="https://calendar.example.test/e", window=window,
    )
    message = poller_core.reminder_message(FACT, context)
    assert message is not None and message.render_version == "owner-ko-v5"
    shown = om.render(message, destination=om.Ref("none"))
    assert shown == f"{FACT} · <https://calendar.example.test/e>"
    assert "evt|" not in shown and "대상:" not in shown and "\n" not in shown


def test_earlier_render_versions_are_unchanged() -> None:
    context = poller_core.ReminderContext(key="k", source=("calendar", "primary"))
    message = poller_core.reminder_message(FACT, context)
    assert message is not None
    from dataclasses import replace
    assert om.render(replace(message, render_version="owner-ko-v1"), destination=om.Ref("none")).startswith(
        "대상: 리마인더 (k)\n")

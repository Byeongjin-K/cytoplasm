"""Shared approval-reminder façade and existing-watcher wiring regressions."""
from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.interop.approval_lease import FileKeyLease, ReminderJournal
from automation.interop.approval_lifecycle import ApprovalRequest, Probe, remind_owner_approval
from automation.interop.approval_reminder import (
    ApprovalReminder, ReminderBoundaryError, ReminderContext, ReminderOutcome, compose_reminder,
)
from automation.interop.owner_message import Space
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import ApprovalKind

_POSTED = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
_REQUEST = ApprovalRequest(
    key="mail:compose:42",
    action_hash="sha256:bound",
    message_id="333",
    channel_id="222",
    created_at="2026-08-21T00:00:00Z",
)


class Observer:
    def __init__(self, decision: Probe = Probe.BOUND_PENDING) -> None:
        self.decision = decision
        self.calls = 0

    def probe(self, request: ApprovalRequest) -> Probe:
        assert request == _REQUEST
        self.calls += 1
        return self.decision


class Delivery:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def __call__(self, channel_id: str, content: str) -> None:
        self.messages.append((channel_id, content))


def _context(tmp_path: Path, delivery: Delivery, now: datetime) -> ReminderContext:
    return ReminderContext(
        config=ApprovalReminderConfig(),
        journal=ReminderJournal(tmp_path / "reminders"),
        request_type=ApprovalKind.MAIL_COMPOSE,
        deliver=delivery,
        clock=lambda: now,
    )


def test_shared_facade_sends_minimum_information_pointer_at_three_hours(tmp_path: Path) -> None:
    delivery = Delivery()
    verdict = remind_owner_approval(
        _REQUEST,
        Observer(),
        FileKeyLease(tmp_path / "leases"),
        _context(tmp_path, delivery, _POSTED + timedelta(hours=3)),
    )

    assert verdict.outcome is ReminderOutcome.SENT
    assert delivery.messages == [
        (
            "222",
            "⏰ 승인 대기 3시간",
        )
    ]


def test_shared_facade_maps_uncertain_probe_fail_closed(tmp_path: Path) -> None:
    delivery = Delivery()
    verdict = remind_owner_approval(
        _REQUEST,
        Observer(Probe.BINDING_MISMATCH),
        FileKeyLease(tmp_path / "leases"),
        _context(tmp_path, delivery, _POSTED + timedelta(hours=4)),
    )
    assert verdict.outcome is ReminderOutcome.DEFERRED
    assert delivery.messages == []


def test_disabled_surface_creates_no_journal_probe_or_send(tmp_path: Path) -> None:
    observer, delivery = Observer(), Delivery()
    context = ReminderContext(
        config=ApprovalReminderConfig(enabled=False),
        journal=ReminderJournal(tmp_path / "reminders"),
        request_type=ApprovalKind.MAIL_COMPOSE,
        deliver=delivery,
        clock=lambda: _POSTED + timedelta(days=1),
    )
    verdict = remind_owner_approval(
        _REQUEST, observer, FileKeyLease(tmp_path / "leases"), context
    )
    assert verdict.outcome is ReminderOutcome.DISABLED
    assert observer.calls == 0
    assert delivery.messages == []
    assert not (tmp_path / "reminders").exists()
    assert not (tmp_path / "leases").exists()


def test_dm_link_is_delivered_when_caller_explicitly_classifies_resolved_channel(tmp_path: Path) -> None:
    # Given a known DM record whose source channel is resolved at send time.
    delivery = Delivery()
    seen: list[str] = []

    def space_for(channel_id: str) -> Space:
        seen.append(channel_id)
        return "dm"

    context = replace(_context(tmp_path, delivery, _POSTED + timedelta(hours=3)),
                      source_channel_id_for=lambda _request: "444", space_for=space_for)
    # When the real lifecycle facade dispatches the due reminder.
    verdict = remind_owner_approval(_REQUEST, Observer(), FileKeyLease(tmp_path / "leases"), context)
    # Then the original rendered bytes and the resolved DM destination are preserved.
    expected = compose_reminder(ApprovalReminder(ApprovalKind.MAIL_COMPOSE, timedelta(hours=3),
                                                "https://discord.com/channels/@me/444/333"))
    assert verdict.outcome is ReminderOutcome.SENT
    assert delivery.messages == [("444", expected)]
    assert seen == ["444"]


@pytest.mark.parametrize("use_resolver", [False, True])
def test_guild_link_wins_when_context_also_has_a_space_callback(tmp_path: Path, use_resolver: bool) -> None:
    # Given known guild metadata and a callback that must not override it.
    delivery = Delivery()

    def forbidden_space(_channel_id: str) -> Space:
        pytest.fail("known guild must not consult the fallback space callback")

    context = replace(_context(tmp_path, delivery, _POSTED + timedelta(hours=3)), guild_id="111",
                      guild_id_for=(lambda _channel: "555") if use_resolver else None,
                      space_for=forbidden_space)
    # When the facade renders through the existing guild resolution path.
    verdict = remind_owner_approval(_REQUEST, Observer(), FileKeyLease(tmp_path / "leases"), context)
    # Stored coordinates win without a lookup; the resolver fills only legacy gaps.
    guild = "111"
    expected = compose_reminder(ApprovalReminder(ApprovalKind.MAIL_COMPOSE, timedelta(hours=3),
                                                f"https://discord.com/channels/{guild}/222/333"))
    assert verdict.outcome is ReminderOutcome.SENT
    assert delivery.messages == [("222", expected)]


def test_reminder_refuses_when_known_guild_coordinate_is_malformed(tmp_path: Path) -> None:
    # Given a due request with a malformed known guild coordinate.
    delivery = Delivery()
    context = replace(_context(tmp_path, delivery, _POSTED + timedelta(hours=3)), guild_id="bad")
    # When the real facade tries to send the pointer.
    with pytest.raises(ReminderBoundaryError):
        remind_owner_approval(_REQUEST, Observer(), FileKeyLease(tmp_path / "leases"), context)
    # Then the existing refusal cannot be misreported as a delivered reminder.
    assert delivery.messages == []


def test_existing_approval_watchers_call_the_shared_facade() -> None:
    root = Path(__file__).parents[2]
    watchers = (
        root / "skills/mail/scripts/triage_cli.py",
        root / "skills/calendar/scripts/confirm_reaction_watch.py",
        root / "skills/todo/scripts/todo_confirm_reaction_watch.py",
        root / "automation/repair/repair_ops_reaction_watch.py",
    )
    for path in watchers:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = (node for node in ast.walk(tree) if isinstance(node, ast.Call))
        assert any(
            (
                isinstance(call.func, ast.Attribute)
                and call.func.attr in ("remind_owner_approval", "dispatch_owner_approval_reminders")
            )
            or (
                isinstance(call.func, ast.Name)
                and call.func.id in ("remind_owner_approval", "dispatch_owner_approval_reminders")
            )
            for call in calls
        ), path

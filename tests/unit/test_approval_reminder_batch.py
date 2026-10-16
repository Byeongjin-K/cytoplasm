"""Per-tick delivery grouping retains card-level scheduling and claims."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.interop.approval_lease import FileKeyLease, ReminderJournal
from automation.interop.approval_reminder import (
    ReminderCall, ReminderContext, ReminderOutcome, dispatch_owner_approval_reminders,
)
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import ApprovalKind
from automation.interop.approval_types import ApprovalRequest, Probe

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


class Pending:
    def probe(self, request: ApprovalRequest) -> Probe:
        return Probe.BOUND_PENDING


def calls_for(
    tmp_path: Path, channels: tuple[str, ...], deliver: Callable[[str, str], None],
) -> tuple[ReminderCall, ...]:
    context = ReminderContext(
        config=ApprovalReminderConfig(), journal=ReminderJournal(tmp_path / "journal"),
        request_type=ApprovalKind.CALENDAR, deliver=deliver, clock=lambda: NOW, guild_id="111",
    )
    return tuple(
        ReminderCall(
            ApprovalRequest(
                f"card:{index}", "sha256:bound", str(333 + index), channel,
                (NOW - timedelta(hours=3)).isoformat(),
            ),
            Pending(), FileKeyLease(tmp_path / "leases"), context,
        )
        for index, channel in enumerate(channels)
    )


@pytest.mark.parametrize("channels", [("222", "222"), ("222", "444"), ("222",)])
def test_due_cards_send_one_line_per_card_in_one_message_per_channel(
    tmp_path: Path, channels: tuple[str, ...],
) -> None:
    # Given: bound pending cards at the same due time.
    posts: list[tuple[str, str]] = []
    calls = calls_for(tmp_path, channels, lambda channel, body: posts.append((channel, body)))
    # When: one caller tick dispatches the reminders.
    verdicts = dispatch_owner_approval_reminders(calls)
    # Then: each destination receives one message, with exactly one line per card.
    expected: dict[str, list[str]] = {}
    for index, channel in enumerate(channels):
        expected.setdefault(channel, []).append(
            f"⏰ 승인 대기 3시간 · https://discord.com/channels/111/{channel}/{333 + index}"
        )
    assert posts == [(channel, "\n".join(lines)) for channel, lines in expected.items()]
    assert all(verdict.outcome is ReminderOutcome.SENT for verdict in verdicts)
    assert all("✅" not in body and "⛔" not in body for _, body in posts)


def test_same_slot_does_not_repeat_batched_cards(tmp_path: Path) -> None:
    # Given: two cards already delivered together in this slot.
    posts: list[tuple[str, str]] = []
    calls = calls_for(tmp_path, ("222", "222"), lambda channel, body: posts.append((channel, body)))
    dispatch_owner_approval_reminders(calls)
    # When: the same tick runs again.
    verdicts = dispatch_owner_approval_reminders(calls)
    # Then: both card markers prevent another post.
    assert len(posts) == 1
    assert [verdict.outcome for verdict in verdicts] == [ReminderOutcome.ALREADY_CLAIMED] * 2


def test_failed_batch_keeps_claims_and_allows_next_slot(tmp_path: Path) -> None:
    # Given: a transport whose uncertain result must not be retried in this slot.
    def fail(channel: str, body: str) -> None:
        raise OSError("uncertain delivery")

    calls = calls_for(tmp_path, ("222", "222"), fail)
    with pytest.raises(OSError, match="uncertain delivery"):
        dispatch_owner_approval_reminders(calls)
    assert [v.outcome for v in dispatch_owner_approval_reminders(calls)] == [
        ReminderOutcome.ALREADY_CLAIMED,
    ] * 2
    posts: list[tuple[str, str]] = []
    later = tuple(replace(call, context=replace(
        call.context, clock=lambda: NOW + timedelta(hours=1),
        deliver=lambda channel, body: posts.append((channel, body)),
    )) for call in calls)
    # When: the next scheduled slot becomes due.
    dispatch_owner_approval_reminders(later)
    # Then: both cards can be delivered together again.
    assert len(posts) == 1 and len(posts[0][1].splitlines()) == 2


def test_batch_holds_card_leases_through_delivery(tmp_path: Path) -> None:
    # Given: the producer takes the same key leases as the reminder dispatcher.
    lease = FileKeyLease(tmp_path / "leases")
    ownership: list[bool] = []

    def deliver(channel: str, body: str) -> None:
        for index in range(2):
            with lease.hold(f"card:{index}") as owned:
                ownership.append(owned)

    calls = calls_for(tmp_path, ("222", "222"), deliver)
    # When: both cards are delivered in one batch.
    dispatch_owner_approval_reminders(calls)
    # Then: neither approval producer can race the grouped send.
    assert ownership == [False, False]
    with lease.hold("card:0") as owned:
        assert owned


def test_channel_failure_does_not_mark_sent_or_block_other_channel(tmp_path: Path) -> None:
    # Given: only one destination fails, and the caller isolates transport errors.
    posts: list[str] = []
    errors: list[str] = []

    def deliver(channel: str, body: str) -> None:
        if channel == "222":
            raise OSError("destination unavailable")
        posts.append(channel)

    calls = calls_for(tmp_path, ("222", "444"), deliver)
    # When: one tick sends both channel groups.
    verdicts = dispatch_owner_approval_reminders(
        calls, on_error=lambda request, error: errors.append(request.key),
    )
    # Then: failure is not reported as sent; the successful card still advances.
    assert errors == ["card:0"]
    assert posts == ["444"]
    assert [verdict.outcome for verdict in verdicts] == [ReminderOutcome.SENT]


def test_the_mail_watch_tick_hands_every_due_draft_to_one_grouped_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two pending mail drafts in one watch tick.
    import sys
    from types import SimpleNamespace

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "mail" / "scripts"))
    import triage_cli

    drafts = [{"id": f"d-{n}", "status": "pending", "message_id": f"m-{n}"} for n in (1, 2)]
    monkeypatch.setattr(triage_cli.triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_cli, "_digest_reactions", lambda: None)
    monkeypatch.setattr(triage_cli.triage_confirm, "owner_id", lambda: "100000000000000001")
    monkeypatch.setattr(triage_cli.triage_gate, "list_drafts", lambda: drafts)
    monkeypatch.setattr(triage_cli.triage_approval, "expire_retired_approval", lambda _d: False)
    monkeypatch.setattr(
        triage_cli, "_remind_pending",
        lambda draft, _config: triage_cli._REMINDER_BATCH.get().append(draft["id"]),
    )
    grouped: list[list[str]] = []
    monkeypatch.setattr(triage_cli.triage_approval, "_repo_module", lambda _name: SimpleNamespace(
        dispatch_owner_approval_reminders=lambda calls, on_error: grouped.append(list(calls))))

    def no_decision(_draft: dict) -> str:
        raise triage_cli.triage_gate.GateError("승인 없음", 1)

    monkeypatch.setattr(triage_cli.triage_confirm, "resolve_reaction", no_decision)
    # When: the tick runs.
    triage_cli.cmd_watch(SimpleNamespace(reminder_config=object()))
    # Then: both drafts reach one grouped dispatch instead of one send each.
    assert grouped == [["d-1", "d-2"]]

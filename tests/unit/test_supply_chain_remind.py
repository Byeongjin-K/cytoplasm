"""Skill-deploy approval reminders reuse the existing supply-chain tick."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from automation.interop.approval_lease import FileKeyLease
from automation.interop.approval_reminder import ReminderOutcome
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import ApprovalSurfaceError
from automation.supply_chain_plan import PendingRequest
from automation.supply_chain_remind import remind_unanswered
from automation.supply_chain_watch import TickResult

_DISCORD_EPOCH_MS = 1_420_070_400_000
_POSTED = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
_CHANNEL_ID = "1528936606946122421"
_GUILD_ID = "1528936606354856737"
_CONFIG = ApprovalReminderConfig(
    initial_delay=timedelta(hours=1),
    repeat_interval=timedelta(hours=1),
)


def _snowflake(moment: datetime) -> str:
    milliseconds = int(moment.timestamp() * 1000)
    return str((milliseconds - _DISCORD_EPOCH_MS) << 22)


def _request(root: Path, *, message_id: str | None = None) -> PendingRequest:
    request = PendingRequest("skill-deploy:demo", "skill-deploy", "demo", "demo")
    pending = root / "pending"
    pending.mkdir(parents=True)
    record = {
        "action_hash": "a" * 64,
        "channel_id": _CHANNEL_ID,
        "hash": "b" * 64,
        "message_id": message_id or _snowflake(_POSTED),
    }
    _ = (pending / "demo.json").write_text(json.dumps(record), encoding="utf-8")
    return request


def _channel_of(record: Mapping[str, str]) -> str:
    return record["channel_id"]


def test_hourly_reminder_links_to_original_approval_message(tmp_path: Path) -> None:
    """Given an hour-old unanswered approval, one tick sends a clickable guild link."""
    request = _request(tmp_path)
    result = TickResult(request, "retain", "unanswered")
    delivered: list[tuple[str, str]] = []

    verdicts = remind_unanswered(
        (result,),
        tmp_path,
        decision_of=lambda _message_id: "absent",
        channel_of=_channel_of,
        deliver=lambda channel_id, body: delivered.append((channel_id, body)),
        guild_of=lambda _channel_id: _GUILD_ID,
        lease=FileKeyLease(tmp_path / "approval-leases"),
        config=_CONFIG,
        clock=lambda: _POSTED + timedelta(hours=1),
        on_error=lambda _key, _reason: None,
    )

    expected_link = (
        f"https://discord.com/channels/{_GUILD_ID}/"
        + f"{_CHANNEL_ID}/{_snowflake(_POSTED)}"
    )
    assert [verdict.outcome for verdict in verdicts] == [ReminderOutcome.SENT]
    assert delivered == [
        (
            _CHANNEL_ID,
            f"⏰ 승인 대기 1시간 · {expected_link}",
        )
    ]


def test_same_slot_is_not_duplicated_and_next_hour_is_due(tmp_path: Path) -> None:
    request = _request(tmp_path)
    result = TickResult(request, "retain", "unanswered")
    delivered: list[tuple[str, str]] = []
    now = [_POSTED + timedelta(hours=1)]

    def run_tick() -> ReminderOutcome:
        return remind_unanswered(
            (result,),
            tmp_path,
            decision_of=lambda _message_id: "absent",
            channel_of=_channel_of,
            deliver=lambda channel_id, body: delivered.append((channel_id, body)),
            guild_of=lambda _channel_id: _GUILD_ID,
            lease=FileKeyLease(tmp_path / "approval-leases"),
            config=_CONFIG,
            clock=lambda: now[0],
            on_error=lambda _key, _reason: None,
        )[0].outcome

    assert run_tick() is ReminderOutcome.SENT
    assert run_tick() is ReminderOutcome.ALREADY_CLAIMED

    now[0] += timedelta(hours=1)
    assert run_tick() is ReminderOutcome.SENT
    assert len(delivered) == 2


def test_young_or_terminal_request_sends_nothing(tmp_path: Path) -> None:
    request = _request(tmp_path)
    result = TickResult(request, "retain", "unanswered")
    delivered: list[tuple[str, str]] = []
    guild_lookups: list[str] = []
    channel_lookups: list[str] = []
    decision = ["absent"]
    now = [_POSTED + timedelta(minutes=59)]

    def run_tick() -> ReminderOutcome:
        return remind_unanswered(
            (result,),
            tmp_path,
            decision_of=lambda _message_id: decision[0],
            channel_of=lambda record: channel_lookups.append(record["channel_id"])
            or record["channel_id"],
            deliver=lambda channel_id, body: delivered.append((channel_id, body)),
            guild_of=lambda channel_id: guild_lookups.append(channel_id) or _GUILD_ID,
            lease=FileKeyLease(tmp_path / "approval-leases"),
            config=_CONFIG,
            clock=lambda: now[0],
            on_error=lambda _key, _reason: None,
        )[0].outcome

    assert run_tick() is ReminderOutcome.NOT_DUE

    decision[0] = "approved"
    now[0] += timedelta(hours=2)
    assert run_tick() is ReminderOutcome.RETIRED
    assert delivered == []
    assert guild_lookups == []
    assert channel_lookups == []


def test_transport_failure_is_contained_and_claim_survives(tmp_path: Path) -> None:
    request = _request(tmp_path)
    result = TickResult(request, "retain", "unanswered")
    errors: list[tuple[str, str]] = []
    attempts = [0]

    def fail_delivery(_channel_id: str, _body: str) -> None:
        attempts[0] += 1
        raise OSError("offline")

    first = remind_unanswered(
        (result,),
        tmp_path,
        decision_of=lambda _message_id: "absent",
        channel_of=_channel_of,
        deliver=fail_delivery,
        guild_of=lambda _channel_id: _GUILD_ID,
        lease=FileKeyLease(tmp_path / "approval-leases"),
        config=_CONFIG,
        clock=lambda: _POSTED + timedelta(hours=1),
        on_error=lambda key, reason: errors.append((key, reason)),
    )
    retry = remind_unanswered(
        (result,),
        tmp_path,
        decision_of=lambda _message_id: "absent",
        channel_of=_channel_of,
        deliver=fail_delivery,
        guild_of=lambda _channel_id: _GUILD_ID,
        lease=FileKeyLease(tmp_path / "approval-leases"),
        config=_CONFIG,
        clock=lambda: _POSTED + timedelta(hours=1),
        on_error=lambda key, reason: errors.append((key, reason)),
    )

    assert first == ()
    assert retry[0].outcome is ReminderOutcome.ALREADY_CLAIMED
    assert attempts == [1]
    assert errors == [("skill-deploy:demo", "OSError")]


def test_bad_record_does_not_starve_other_unanswered_requests(tmp_path: Path) -> None:
    bad = _request(tmp_path, message_id="not-a-snowflake")
    good = PendingRequest("skill-deploy:good", "skill-deploy", "good", "good")
    record = {
        "action_hash": "c" * 64,
        "channel_id": _CHANNEL_ID,
        "hash": "d" * 64,
        "message_id": _snowflake(_POSTED),
    }
    _ = (tmp_path / "pending" / "good.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    errors: list[tuple[str, str]] = []
    delivered: list[tuple[str, str]] = []

    verdicts = remind_unanswered(
        (
            TickResult(bad, "retain", "unanswered"),
            TickResult(good, "retain", "unanswered"),
        ),
        tmp_path,
        decision_of=lambda _message_id: "absent",
        channel_of=_channel_of,
        deliver=lambda channel_id, body: delivered.append((channel_id, body)),
        guild_of=lambda _channel_id: _GUILD_ID,
        lease=FileKeyLease(tmp_path / "approval-leases"),
        config=_CONFIG,
        clock=lambda: _POSTED + timedelta(hours=1),
        on_error=lambda key, reason: errors.append((key, reason)),
    )

    assert [verdict.outcome for verdict in verdicts] == [ReminderOutcome.SENT]
    assert errors == [("skill-deploy:demo", "ReminderRecordError")]
    assert len(delivered) == 1


def test_overflowing_snowflake_is_isolated_as_a_bad_record(tmp_path: Path) -> None:
    request = _request(tmp_path, message_id="9" * 1000)
    errors: list[tuple[str, str]] = []

    verdicts = remind_unanswered(
        (TickResult(request, "retain", "unanswered"),),
        tmp_path,
        decision_of=lambda _message_id: "absent",
        channel_of=_channel_of,
        deliver=lambda _channel_id, _body: None,
        guild_of=lambda _channel_id: _GUILD_ID,
        lease=FileKeyLease(tmp_path / "approval-leases"),
        config=_CONFIG,
        clock=lambda: _POSTED + timedelta(hours=1),
        on_error=lambda key, reason: errors.append((key, reason)),
    )

    assert verdicts == ()
    assert errors == [("skill-deploy:demo", "ReminderRecordError")]


def test_unvalidated_record_channel_is_never_a_delivery_target(tmp_path: Path) -> None:
    request = _request(tmp_path)
    errors: list[tuple[str, str]] = []
    delivered: list[tuple[str, str]] = []

    def reject_stored_binding(_record: Mapping[str, str]) -> str:
        raise ApprovalSurfaceError("stored approval surface is invalid")

    verdicts = remind_unanswered(
        (TickResult(request, "retain", "unanswered"),),
        tmp_path,
        decision_of=lambda _message_id: "absent",
        channel_of=reject_stored_binding,
        deliver=lambda channel_id, body: delivered.append((channel_id, body)),
        guild_of=lambda _channel_id: _GUILD_ID,
        lease=FileKeyLease(tmp_path / "approval-leases"),
        config=_CONFIG,
        clock=lambda: _POSTED + timedelta(hours=1),
        on_error=lambda key, reason: errors.append((key, reason)),
    )

    assert verdicts == ()
    assert delivered == []
    assert errors == [("skill-deploy:demo", "ApprovalSurfaceError")]


def test_disabled_reminders_never_validate_the_stored_channel(tmp_path: Path) -> None:
    request = _request(tmp_path)
    lookups: list[str] = []

    verdicts = remind_unanswered(
        (TickResult(request, "retain", "unanswered"),),
        tmp_path,
        decision_of=lambda _message_id: "absent",
        channel_of=lambda record: lookups.append(record["channel_id"])
        or record["channel_id"],
        deliver=lambda _channel_id, _body: None,
        guild_of=lambda _channel_id: _GUILD_ID,
        lease=FileKeyLease(tmp_path / "approval-leases"),
        config=ApprovalReminderConfig(enabled=False),
        clock=lambda: _POSTED + timedelta(days=1),
        on_error=lambda _key, _reason: None,
    )

    assert verdicts[0].outcome is ReminderOutcome.DISABLED
    assert lookups == []

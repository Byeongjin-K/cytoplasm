"""Release cards share the supply-chain reminder tick; other kinds stay out."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from automation.interop.approval_lease import FileKeyLease
from automation.interop.approval_reminder import ReminderOutcome
from automation.interop.approval_reminder_config import ApprovalReminderConfig
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


def _channel_of(record: Mapping[str, str]) -> str:
    return record["channel_id"]


def _write_pending(root: Path, record_name: str) -> None:
    pending = root / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    record = {
        "action_hash": "a" * 64,
        "channel_id": _CHANNEL_ID,
        "hash": "b" * 64,
        "message_id": _snowflake(_POSTED),
    }
    _ = (pending / f"{record_name}.json").write_text(json.dumps(record), encoding="utf-8")


def _pending(kind: str, name: str) -> PendingRequest:
    return PendingRequest(f"{kind}:{name}", kind, name, name)


def _tick(
    root: Path,
    results: tuple[TickResult, ...],
    *,
    delivered: list[tuple[str, str]],
    now: datetime,
) -> tuple[ReminderOutcome, ...]:
    return tuple(
        verdict.outcome
        for verdict in remind_unanswered(
            results,
            root,
            decision_of=lambda _message_id: "absent",
            channel_of=_channel_of,
            deliver=lambda channel_id, body: delivered.append((channel_id, body)),
            guild_of=lambda _channel_id: _GUILD_ID,
            lease=FileKeyLease(root / "approval-leases"),
            config=_CONFIG,
            clock=lambda: now,
            on_error=lambda _key, _reason: None,
        )
    )


def test_release_older_than_interval_gets_one_reminder_per_interval(
    tmp_path: Path,
) -> None:
    """Given an unanswered RELEASE card older than the cadence, one pointer per slot."""
    _write_pending(tmp_path, "demo")
    result = TickResult(_pending("release", "demo"), "retain", "unanswered")
    delivered: list[tuple[str, str]] = []
    now = [_POSTED + timedelta(hours=1)]

    def run_tick() -> tuple[ReminderOutcome, ...]:
        return _tick(tmp_path, (result,), delivered=delivered, now=now[0])

    assert run_tick() == (ReminderOutcome.SENT,)
    assert run_tick() == (ReminderOutcome.ALREADY_CLAIMED,)

    now[0] += timedelta(hours=1)
    assert run_tick() == (ReminderOutcome.SENT,)
    assert len(delivered) == 2
    expected_link = (
        f"https://discord.com/channels/{_GUILD_ID}/"
        + f"{_CHANNEL_ID}/{_snowflake(_POSTED)}"
    )
    assert delivered[0] == (
        _CHANNEL_ID,
        f"⏰ 승인 대기 1시간 · [승인 카드](<{expected_link}>)",
    )


def test_skill_deploy_reminder_uses_shared_one_line(tmp_path: Path) -> None:
    """Given an unanswered skill-deploy card, send the shared compact pointer."""
    _write_pending(tmp_path, "demo")
    result = TickResult(_pending("skill-deploy", "demo"), "retain", "unanswered")
    delivered: list[tuple[str, str]] = []

    outcomes = _tick(
        tmp_path,
        (result,),
        delivered=delivered,
        now=_POSTED + timedelta(hours=1),
    )

    expected_link = (
        f"https://discord.com/channels/{_GUILD_ID}/"
        + f"{_CHANNEL_ID}/{_snowflake(_POSTED)}"
    )
    assert outcomes == (ReminderOutcome.SENT,)
    assert delivered == [
        (
            _CHANNEL_ID,
            f"⏰ 승인 대기 1시간 · [승인 카드](<{expected_link}>)",
        )
    ]


def test_other_kinds_are_still_ignored(tmp_path: Path) -> None:
    """Given unanswered cards of other kinds, the supply-chain tick sends nothing."""
    kinds = ("skill-publish", "todo", "mail-reply", "repair")
    results: list[TickResult] = []
    for kind in kinds:
        name = kind.replace("-", "")
        _write_pending(tmp_path, name)
        results.append(TickResult(_pending(kind, name), "retain", "unanswered"))
    delivered: list[tuple[str, str]] = []

    outcomes = _tick(
        tmp_path,
        tuple(results),
        delivered=delivered,
        now=_POSTED + timedelta(hours=1),
    )

    assert outcomes == ()
    assert delivered == []

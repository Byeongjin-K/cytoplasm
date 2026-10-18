"""Hourly pointers for unanswered skill-deploy and release approvals in the existing watcher tick."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol, TypeAlias

from automation.approval_kpi.policy_table import POLICY_TABLE
from automation.interop.approval_lease import (
    ApprovalLease,
    ReminderJournal,
    ReminderJournalError,
)
from automation.interop.approval_reminder import (
    ReminderCall, ReminderContext, ReminderVerdict, dispatch_owner_approval_reminders,
)
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import ApprovalKind, ApprovalSurfaceError
from automation.interop.approval_types import ApprovalRequest, Probe
from automation.supply_chain_watch import TickResult

DecisionOf = Callable[[str], str]
Deliver = Callable[[str, str], None]
GuildOf = Callable[[str], str]
ChannelOf = Callable[[Mapping[str, str]], str]
Clock = Callable[[], datetime]
ReportError = Callable[[str, str], None]
JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...

_DISCORD_EPOCH_MS: Final = 1_420_070_400_000
_JSON_LOADS: JsonLoader = json.loads
_DECISION_PROBES: Final = {
    "approved": Probe.APPROVED,
    "denied": Probe.CANCELLED,
    "absent": Probe.BOUND_PENDING,
    "missing": Probe.MISSING,
}


class ReminderRecordError(ValueError):
    """A pending record cannot identify its original approval message."""


@dataclass(frozen=True, slots=True)
class _DecisionWatcher:
    decision_of: DecisionOf

    def probe(self, request: ApprovalRequest) -> Probe:
        return _DECISION_PROBES.get(
            self.decision_of(request.message_id), Probe.UNVERIFIABLE
        )

    def apply(self, request: ApprovalRequest, decision: Probe) -> None:
        del request, decision
        raise ReminderRecordError("a reminder watcher cannot apply an owner decision")

    def drop(self, request: ApprovalRequest) -> None:
        del request
        raise ReminderRecordError("a reminder watcher cannot drop an approval record")


@dataclass(frozen=True, slots=True)
class _StoredChannel:
    record: Mapping[str, str]
    channel_of: ChannelOf
    verified: str | None = None

    def __call__(self, _request: ApprovalRequest) -> str:
        return self.verified if self.verified is not None else self.channel_of(self.record)


def _field(record: Mapping[str, str], name: str, *, required: bool = True) -> str:
    value = record.get(name, "")
    if required and not value:
        raise ReminderRecordError(f"pending record has no {name}")
    return value


def _created_at(message_id: str) -> str:
    if not message_id.isascii() or not message_id.isdigit():
        raise ReminderRecordError("pending record message_id is not a Discord snowflake")
    snowflake = int(message_id)
    if snowflake.bit_length() > 64:
        raise ReminderRecordError("pending record message_id exceeds a Discord snowflake")
    timestamp_ms = (snowflake >> 22) + _DISCORD_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _request(
    gate_dir: Path, result: TickResult
) -> tuple[ApprovalRequest, Mapping[str, str]]:
    path = gate_dir / "pending" / f"{result.request.record_name}.json"
    try:
        decoded = _JSON_LOADS(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReminderRecordError(str(path)) from error
    if not isinstance(decoded, dict) or any(
        not isinstance(value, str) for value in decoded.values()
    ):
        raise ReminderRecordError(str(path))
    record = {
        key: value
        for key, value in decoded.items()
        if isinstance(value, str)
    }
    record.setdefault("kind", result.request.kind)
    if record["kind"] != result.request.kind:
        raise ReminderRecordError("pending record kind contradicts its request")
    message_id = _field(record, "message_id")
    return (
        ApprovalRequest(
            key=result.request.key,
            action_hash=_field(record, "action_hash", required=False),
            message_id=message_id,
            channel_id=_field(record, "channel_id", required=False),
            created_at=_created_at(message_id),
        ),
        record,
    )


_REMINDED_KINDS: Final = frozenset({ApprovalKind.SKILL_DEPLOY, ApprovalKind.RELEASE})


def _config_for(kind: ApprovalKind, config: ApprovalReminderConfig) -> ApprovalReminderConfig:
    """Kind cadence from the policy table; UNKNOWN TTL keeps the skill-deploy interval."""
    for entry in POLICY_TABLE:
        if entry.kind != kind.value:
            continue
        if entry.ttl_seconds is None:
            return config
        interval = timedelta(seconds=entry.ttl_seconds)
        return ApprovalReminderConfig(
            enabled=config.enabled,
            initial_delay=interval,
            repeat_interval=interval,
        )
    return config


def remind_unanswered(
    results: tuple[TickResult, ...],
    gate_dir: Path,
    *,
    decision_of: DecisionOf,
    channel_of: ChannelOf,
    deliver: Deliver,
    guild_of: GuildOf,
    lease: ApprovalLease,
    config: ApprovalReminderConfig,
    clock: Clock,
    on_error: ReportError,
    on_sent: Callable[[str], None] | None = None,
) -> tuple[ReminderVerdict, ...]:
    """Send due pointers only for records this tick proved are still unanswered."""
    reminders: list[ReminderCall] = []
    for result in results:
        try:
            kind = ApprovalKind(result.request.kind)
        except ValueError:
            continue
        if (
            kind not in _REMINDED_KINDS
            or result.outcome != "retain"
            or result.reason != "unanswered"
        ):
            continue
        try:
            request, record = _request(gate_dir, result)
            # Validate the record's own kind/channel before claiming a delivery slot.
            # Invalid bindings must remain visible on subsequent ticks, not disappear
            # behind an uncertain-delivery claim.
            cadence = _config_for(kind, config)
            due = clock() >= datetime.fromisoformat(request.created_at.replace("Z", "+00:00")) + cadence.initial_delay
            channel_id = (channel_of(record) if config.enabled and due
                          and decision_of(request.message_id) == "absent" else None)
            context = ReminderContext(
                config=cadence,
                journal=ReminderJournal(gate_dir / "reminder-journal"),
                request_type=kind,
                deliver=deliver,
                clock=clock,
                guild_id_for=guild_of,
                source_channel_id_for=_StoredChannel(record, channel_of, channel_id),
            )
            reminders.append(ReminderCall(request, _DecisionWatcher(decision_of), lease, context))
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            ApprovalSurfaceError,
            ReminderJournalError,
        ) as error:
            on_error(result.request.key, type(error).__name__)
    def reminder_failed(request: ApprovalRequest, error: Exception) -> None:
        if not isinstance(error, (
            OSError, ValueError, KeyError, TypeError, ApprovalSurfaceError, ReminderJournalError,
        )):
            raise error
        on_error(request.key, type(error).__name__)

    return dispatch_owner_approval_reminders(
        reminders, on_error=reminder_failed,
        on_sent=(lambda request: on_sent(request.key)) if on_sent is not None else None,
    )

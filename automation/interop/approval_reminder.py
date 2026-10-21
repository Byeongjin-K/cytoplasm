"""Shared scheduling, direct-link, and delivery policy for approval reminders.

This module does not create another approval state machine. Existing approval watchers
observe lifecycle state and use minimum-information links to the original message.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, assert_never
from urllib.parse import urlsplit

from automation.interop.approval_lease import ApprovalLease, ReminderJournal
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from .approval_types import ApprovalRequest, Probe
from automation.interop.approval_surface import ApprovalKind
from .owner_message import (
    LinkResult as LinkResult, LinkStatus as LinkStatus, Space, discord_link, masked_discord_link,
)


class ReminderStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    EXECUTED = "executed"
    DISCARDED = "discarded"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    SOURCE_DELETED = "source-deleted"
    UNVERIFIABLE = "unverifiable"


_TERMINAL = frozenset({
    ReminderStatus.APPROVED,
    ReminderStatus.CANCELLED,
    ReminderStatus.EXECUTED,
    ReminderStatus.DISCARDED,
    ReminderStatus.EXPIRED,
    ReminderStatus.SUPERSEDED,
    ReminderStatus.SOURCE_DELETED,
})


ReminderConfig = ApprovalReminderConfig


class ReminderObserver(Protocol):
    def status_for(self, request: ApprovalRequest) -> ReminderStatus: ...


class ReminderSender(Protocol):
    def send(self, request: ApprovalRequest, slot: int, due_at: datetime) -> None: ...


class ReminderOutcome(StrEnum):
    SENT = "sent"
    NOT_DUE = "not-due"
    ALREADY_CLAIMED = "already-claimed"
    RETIRED = "retired"
    DEFERRED = "deferred"
    SKIPPED = "skipped"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ReminderVerdict:
    outcome: ReminderOutcome
    slot: int | None = None
    due_at: datetime | None = None
    status: ReminderStatus | None = None


def _parse_posted_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("approval created_at must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("approval created_at must include a timezone")
    return parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def due_slot(posted_at: datetime, now: datetime, config: ReminderConfig) -> int | None:
    """Return the latest due slot, anchored to ``posted_at`` (catch-up coalesces)."""
    posted_at, now = posted_at.astimezone(UTC), now.astimezone(UTC)
    first_due = posted_at + config.initial_delay
    if now < first_due:
        return None
    return int((now - first_due) // config.repeat_interval)


def slot_due_at(posted_at: datetime, slot: int, config: ReminderConfig) -> datetime:
    if slot < 0:
        raise ValueError("slot must not be negative")
    return posted_at.astimezone(UTC) + config.initial_delay + slot * config.repeat_interval


def dispatch_due_reminder(
    request: ApprovalRequest,
    observer: ReminderObserver,
    sender: ReminderSender,
    lease: ApprovalLease,
    journal: ReminderJournal,
    config: ReminderConfig,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ReminderVerdict:
    """Observe lifecycle and send at most one reminder for the latest due slot.

    The existing approval-key lease covers observation, durable claim, and send. Thus
    producer/watcher and watcher/watcher races cannot duplicate the same interval.
    """
    if not config.enabled:
        return ReminderVerdict(ReminderOutcome.DISABLED)
    now = clock().astimezone(UTC)
    _parse_posted_at(request.created_at)
    with lease.hold(request.key) as owned:
        if not owned:
            return ReminderVerdict(ReminderOutcome.SKIPPED)
        verdict = _claim_due_reminder(request, observer, journal, config, now)
        if verdict.outcome is ReminderOutcome.SENT:
            assert verdict.slot is not None and verdict.due_at is not None
            sender.send(request, verdict.slot, verdict.due_at)
            _mark_sent(request, verdict, journal, config, now)
        return verdict


def _claim_due_reminder(
    request: ApprovalRequest, observer: ReminderObserver, journal: ReminderJournal,
    config: ReminderConfig, now: datetime,
) -> ReminderVerdict:
    posted_at = _parse_posted_at(request.created_at)
    if journal.is_retired(request.key, request.message_id):
        return ReminderVerdict(ReminderOutcome.RETIRED)
    status = observer.status_for(request)
    match status:
        case ReminderStatus.UNVERIFIABLE:
            return ReminderVerdict(ReminderOutcome.DEFERRED, status=status)
        case ReminderStatus.PENDING:
            pass
        case terminal if terminal in _TERMINAL:
            journal.retire(request.key, request.message_id, terminal.value, _timestamp(now))
            return ReminderVerdict(ReminderOutcome.RETIRED, status=terminal)
        case unexpected:
            raise ValueError(f"unknown reminder status: {unexpected!r}")
    slot = due_slot(posted_at, now, config)
    if slot is None:
        return ReminderVerdict(ReminderOutcome.NOT_DUE)
    due_at = slot_due_at(posted_at, slot, config)
    next_due = slot_due_at(posted_at, slot + 1, config)
    if not journal.claim(
        request.key, request.message_id, slot, _timestamp(due_at),
        _timestamp(next_due), _timestamp(now),
    ):
        return ReminderVerdict(ReminderOutcome.ALREADY_CLAIMED, slot=slot, due_at=due_at)
    # Claims survive send failures: uncertain delivery must not duplicate a slot.
    return ReminderVerdict(ReminderOutcome.SENT, slot=slot, due_at=due_at)


def _mark_sent(
    request: ApprovalRequest, verdict: ReminderVerdict, journal: ReminderJournal,
    config: ReminderConfig, now: datetime,
) -> None:
    assert verdict.slot is not None
    next_due = slot_due_at(_parse_posted_at(request.created_at), verdict.slot + 1, config)
    journal.mark_sent(
        request.key, request.message_id, verdict.slot, _timestamp(now), _timestamp(next_due)
    )


class ReminderBoundaryError(ValueError):
    """Reminder metadata cannot be rendered without crossing the boundary."""


class SourceLifecycle(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class DeliveryRoute(StrEnum):
    OWNER = "owner"
    ORIGINAL = "original"


@dataclass(frozen=True, slots=True)
class DiscordSource:
    """채널/스레드 또는 메시지 좌표. DM은 space로 명시한다.

    space=None은 생략 표식: guild_id가 있으면 guild, 없으면 unknown.
    명시한 space는 guild_id보다 우선하며 링크 생성은 공용 정의에 위임한다.
    """

    channel_id: str
    message_id: str | None = None
    guild_id: str | None = None
    space: Space | None = None


@dataclass(frozen=True, slots=True)
class SourceReference:
    platform: str
    permalink: str | None = None
    discord: DiscordSource | None = None
    lifecycle: SourceLifecycle = SourceLifecycle.ACTIVE


@dataclass(frozen=True, slots=True)
class ApprovalReminder:
    request_type: ApprovalKind
    elapsed: timedelta
    source_url: str | None


@dataclass(frozen=True, slots=True)
class DeliveryScope:
    source_channel_id: str
    owner_dm_channel_id: str


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    channel_id: str
    route: DeliveryRoute


def _snowflake(value: str | None) -> bool:
    return isinstance(value, str) and bool(value) and value.isascii() and value.isdigit()


def _safe_https_url(value: str) -> bool:
    if not value or any(character in value for character in ("\r", "\n", "\t")):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def discord_message_link(source: DiscordSource) -> LinkResult:
    """공용 링크 정의의 소비자. 길드 미상은 DM으로 추측하지 않는다."""
    space = source.space
    if space is None:
        space = "guild" if source.guild_id is not None else "unknown"
    return discord_link(space=space, guild_id=source.guild_id,
                        channel_id=source.channel_id, message_id=source.message_id)


def resolve_source_link(source: SourceReference) -> LinkResult:
    """Resolve a direct source link, failing explicitly rather than copying payload."""
    if source.lifecycle is SourceLifecycle.DELETED:
        return LinkResult(LinkStatus.DELETED, detail="original approval message was deleted")
    platform = source.platform.strip().casefold()
    if platform == "discord":
        if source.discord is None:
            return LinkResult(LinkStatus.INVALID, detail="source coordinates are missing")
        return discord_message_link(source.discord)
    if source.permalink is not None:
        if _safe_https_url(source.permalink):
            return LinkResult(LinkStatus.AVAILABLE, source.permalink)
        return LinkResult(LinkStatus.INVALID, detail="source permalink is invalid")
    return LinkResult(
        LinkStatus.UNAVAILABLE,
        detail="this approval surface does not provide a direct link",
    )


def _request_type_text(value: object) -> str:
    if not isinstance(value, ApprovalKind):
        raise ReminderBoundaryError("request type must be a known approval kind")
    return value.value


def _validate_source_url(value: str) -> str:
    if not _safe_https_url(value):
        raise ReminderBoundaryError("source link must be a safe HTTPS permalink")
    return value


def _elapsed_text(elapsed: timedelta) -> str:
    seconds = elapsed.total_seconds()
    if seconds < 0:
        raise ReminderBoundaryError("elapsed time cannot be negative")
    return f"{int(seconds // 3600)}시간"


def compose_reminder(reminder: ApprovalReminder) -> str:
    """한 카드 한 줄. 좌표 미상일 때는 링크만 생략한다."""
    _request_type_text(reminder.request_type)
    link_line = ""
    if reminder.source_url is not None:
        link_line = f" · {masked_discord_link(_validate_source_url(reminder.source_url), '승인 카드')}"
    elapsed = _elapsed_text(reminder.elapsed)
    return f"⏰ 승인 대기 {elapsed}{link_line}"


def authorize_delivery(scope: DeliveryScope, target: DeliveryTarget) -> bool:
    """Allow only policy-confined endpoints with a matching route and id."""
    if not _snowflake(target.channel_id):
        return False
    owner_route = (
        target.route is DeliveryRoute.OWNER
        and target.channel_id == scope.owner_dm_channel_id
    )
    original_route = (
        target.route is DeliveryRoute.ORIGINAL
        and target.channel_id == scope.source_channel_id
    )
    return owner_route or original_route


class ApprovalProbe(Protocol):
    def probe(self, request: ApprovalRequest) -> Probe: ...


def stored_reminder_space(surface: object) -> Space:
    """Only an explicit persisted DM surface permits the DM link namespace."""
    return "dm" if surface == "owner-dm" else "unknown"


def channel_guild_resolver(
    fetch_channel: Callable[[str], object],
) -> Callable[[str], str | None]:
    """Cache channel guild ids so server/thread links open inside their guild.

    Each resolver fetches a channel at most once. No guild means unknown unless the
    stored surface explicitly identifies a DM. Fetch failures propagate, never guess.
    """
    cache: dict[str, str | None] = {}

    def resolve(channel_id: str) -> str | None:
        if channel_id not in cache:
            match fetch_channel(channel_id):
                case {"guild_id": str(guild_id)}:
                    cache[channel_id] = guild_id or None
                case Mapping():
                    cache[channel_id] = None
                case _:
                    raise ReminderBoundaryError("Discord channel response must be a mapping")
        return cache[channel_id]

    return resolve


@dataclass(frozen=True, slots=True)
class ReminderContext:
    """Injected watcher runtime; no config, clock, state, or transport is global."""

    config: ApprovalReminderConfig
    journal: ReminderJournal
    request_type: ApprovalKind
    deliver: Callable[[str, str], None]
    clock: Callable[[], datetime]
    guild_id: str | None = None
    guild_id_for: Callable[[str], str | None] | None = None
    source_channel_id_for: Callable[[ApprovalRequest], str] | None = None
    space_for: Callable[[str], Space] | None = None


@dataclass(frozen=True, slots=True)
class _LifecycleObserver:
    watcher: ApprovalProbe

    def status_for(self, request: ApprovalRequest) -> ReminderStatus:
        decision = self.watcher.probe(request)
        return {
            Probe.BOUND_PENDING: ReminderStatus.PENDING,
            Probe.APPROVED: ReminderStatus.APPROVED,
            Probe.CANCELLED: ReminderStatus.CANCELLED,
            Probe.MISSING: ReminderStatus.SOURCE_DELETED,
            Probe.BINDING_MISMATCH: ReminderStatus.UNVERIFIABLE,
            Probe.UNVERIFIABLE: ReminderStatus.UNVERIFIABLE,
        }[decision]


@dataclass(frozen=True, slots=True)
class _PointerSender:
    context: ReminderContext

    def send(self, request: ApprovalRequest, slot: int, due_at: datetime) -> None:
        del slot, due_at
        channel_id, body = self.pointer(request)
        self.context.deliver(channel_id, body)

    def pointer(self, request: ApprovalRequest) -> tuple[str, str]:
        source_channel_id = (
            self.context.source_channel_id_for(request)
            if self.context.source_channel_id_for is not None
            else request.channel_id
        )
        guild_id = self.context.guild_id
        if guild_id is None and self.context.guild_id_for is not None:
            guild_id = self.context.guild_id_for(source_channel_id)
        space: Space = "guild" if guild_id is not None else "unknown"
        if guild_id is None and self.context.space_for is not None:
            space = self.context.space_for(source_channel_id)
        link = discord_message_link(
            DiscordSource(source_channel_id, request.message_id, guild_id, space=space)
        )
        match link.status:
            case LinkStatus.AVAILABLE | LinkStatus.UNAVAILABLE:
                pass
            case LinkStatus.INVALID | LinkStatus.DELETED:
                raise ReminderBoundaryError("original approval link is unavailable")
            case _:
                assert_never(link.status)
        target = DeliveryTarget(source_channel_id, DeliveryRoute.ORIGINAL)
        scope = DeliveryScope(source_channel_id, source_channel_id)
        if not authorize_delivery(scope, target):
            raise ReminderBoundaryError("reminder target is outside the approval surface")
        elapsed = self.context.clock().astimezone(UTC) - _parse_posted_at(request.created_at)
        body = compose_reminder(
            ApprovalReminder(self.context.request_type, elapsed, link.url)
        )
        return target.channel_id, body


def dispatch_owner_approval_reminder(
    request: ApprovalRequest,
    watcher: ApprovalProbe,
    lease: ApprovalLease,
    context: ReminderContext,
) -> ReminderVerdict:
    """Run reminder policy inside an existing approval watcher's tick."""
    return dispatch_due_reminder(
        request,
        _LifecycleObserver(watcher),
        _PointerSender(context),
        lease,
        context.journal,
        context.config,
        clock=context.clock,
    )


@dataclass(frozen=True, slots=True)
class ReminderCall:
    request: ApprovalRequest
    watcher: ApprovalProbe
    lease: ApprovalLease
    context: ReminderContext


def dispatch_owner_approval_reminders(
    calls: Iterable[ReminderCall],
    *,
    on_error: Callable[[ApprovalRequest, Exception], None] | None = None,
    on_sent: Callable[[ApprovalRequest], None] | None = None,
) -> tuple[ReminderVerdict, ...]:
    """Claim each card under its existing lease, then send once per destination.

    All leases remain held through delivery and per-card sent markers. A failed
    channel keeps its claims but no sent markers, just like a failed single send.
    """
    verdicts: list[ReminderVerdict | None] = []
    groups: dict[str, list[tuple[ReminderCall, ReminderVerdict, datetime, str, int]]] = {}
    with ExitStack() as leases:
        for call in calls:
            request, context = call.request, call.context
            try:
                if not context.config.enabled:
                    verdicts.append(ReminderVerdict(ReminderOutcome.DISABLED))
                    continue
                now = context.clock().astimezone(UTC)
                _parse_posted_at(request.created_at)
                if not leases.enter_context(call.lease.hold(request.key)):
                    verdicts.append(ReminderVerdict(ReminderOutcome.SKIPPED))
                    continue
                verdict = _claim_due_reminder(
                    request, _LifecycleObserver(call.watcher), context.journal, context.config, now
                )
                if verdict.outcome is ReminderOutcome.SENT:
                    assert verdict.slot is not None and verdict.due_at is not None
                    channel, body = _PointerSender(context).pointer(request)
                    groups.setdefault(channel, []).append((call, verdict, now, body, len(verdicts)))
                verdicts.append(verdict)
            except Exception as error:
                if on_error is None:
                    raise
                on_error(request, error)
        for channel, group in groups.items():
            try:
                group[0][0].context.deliver(channel, "\n".join(item[3] for item in group))
            except Exception as error:
                if on_error is None:
                    raise
                for call, _, _, _, index in group:
                    verdicts[index] = None
                    on_error(call.request, error)
                continue
            for call, verdict, now, _, index in group:
                try:
                    _mark_sent(call.request, verdict, call.context.journal, call.context.config, now)
                    if on_sent is not None:
                        on_sent(call.request)
                except Exception as error:
                    if on_error is None:
                        raise
                    verdicts[index] = None
                    on_error(call.request, error)
    return tuple(verdict for verdict in verdicts if verdict is not None)

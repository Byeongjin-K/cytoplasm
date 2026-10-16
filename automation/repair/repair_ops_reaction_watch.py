"""Fail-closed owner-reaction polling for pending ops repair approvals."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol, assert_never

from automation.interop.approval_lease import FileKeyLease, ReminderJournal
from automation.interop.approval_lifecycle import (
    ApprovalRequest,
    Probe,
    resolve_owner_decision,
)
from automation.interop.approval_reminder import (
    ReminderCall, ReminderContext, channel_guild_resolver,
    dispatch_owner_approval_reminder, dispatch_owner_approval_reminders, stored_reminder_space,
)
from automation.interop.approval_reminder_config import (
    ApprovalReminderConfig,
    load_approval_reminder_config,
)
from automation.interop.approval_surface import ApprovalKind, reaction_instruction, required_surface
from automation.repair.repair_ops_approval_gate import (
    lease_root,
    owner_reacted,
    probe_pending,
    repair_approval_key,
    request_of,
)
from automation.repair.repair_approval_content import approval_content_matches
from automation.repair.repair_ops_discord import RepairDiscordApi
from automation.repair.repair_redaction import redact
from automation.repair.repair_ops_pending import CANCEL_EMOJI, APPROVE_EMOJI, PendingRepairApproval, PendingRepairApprovalStore


APPROVAL_TTL = timedelta(hours=24)


class ReactionDecision(StrEnum):
    """The only outcomes a bound owner reaction can produce."""

    PENDING = "pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    INVALID = "invalid"


class ApprovalPollTransport(Protocol):
    """Read only the posted approval content and its two terminal reaction lists."""

    def content(self, message_id: str) -> str: ...

    def reaction_users(self, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]: ...


class RepairApprovalCommands(Protocol):
    """Run the existing repair lifecycle only after a watcher verdict."""

    def apply(self, pending: PendingRepairApproval) -> bool: ...

    def discard(self, pending: PendingRepairApproval, reason: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class CliRepairApprovalCommands:
    """Invoke the ops repair CLI with the resolved Discord credential only in child env."""

    repair_cli: Path
    token: str

    def apply(self, pending: PendingRepairApproval) -> bool:
        """Revalidate the owner reaction in the child before existing lifecycle apply."""
        return self._run("--apply-approved", pending.ticket_id)

    def discard(self, pending: PendingRepairApproval, reason: str) -> bool:
        """Reopen a cancelled or expired ticket only after this watcher reached a terminal verdict."""
        return self._run("--discard-pending", pending.ticket_id, reason)

    def _run(self, *arguments: str) -> bool:
        environment = dict(os.environ)
        environment["DISCORD_BOT_TOKEN"] = self.token
        result = subprocess.run(
            (sys.executable, str(self.repair_cli), *arguments),
            capture_output=True,
            check=False,
            cwd=str(self.repair_cli.parents[2]),
            env=environment,
            text=True,
            timeout=900,
        )
        if result.stdout:
            print(redact(result.stdout), end="", flush=True)
        if result.stderr:
            print(redact(result.stderr), end="", file=sys.stderr, flush=True)
        return result.returncode == 0


def reaction_decision(pending: PendingRepairApproval, owner_id: str, discord: ApprovalPollTransport) -> ReactionDecision:
    """Return an owner-only, content-bound verdict with cancellation precedence."""
    if not approval_content_matches(pending, discord.content(pending.message_id)):
        return ReactionDecision.INVALID
    if owner_reacted(discord.reaction_users(pending.message_id, CANCEL_EMOJI), owner_id):
        return ReactionDecision.CANCELLED
    if owner_reacted(discord.reaction_users(pending.message_id, APPROVE_EMOJI), owner_id):
        return ReactionDecision.APPROVED
    return ReactionDecision.PENDING


@dataclass(frozen=True, slots=True)
class RepairApprovalWatcher:
    """Poll only reaction lists and retain uncertain work for a future safe tick."""

    store: PendingRepairApprovalStore
    discord: ApprovalPollTransport
    commands: RepairApprovalCommands
    owner_id: str
    approval_log: Path
    now: Callable[[], datetime]
    reminder_config: ApprovalReminderConfig | None = None

    def run_once(self) -> None:
        """Process every active request without accepting unbound or ambiguous reactions."""
        reminders: list[ReminderCall] = []
        resolutions: list[Callable[[], None]] = []
        for pending in self.store.all():
            self._process(pending, reminders, resolutions)
        dispatch_owner_approval_reminders(reminders)
        for resolve in resolutions:
            resolve()

    def _process(
        self, pending: PendingRepairApproval, reminders: list[ReminderCall] | None = None,
        resolutions: list[Callable[[], None]] | None = None,
    ) -> None:
        if self.now().astimezone(UTC) - pending.created_at.astimezone(UTC) >= APPROVAL_TTL:
            self._expire(pending)
            return
        discord = self._discord_for(pending)
        binding = discord.binding if isinstance(discord, RepairDiscordApi) else None
        request = request_of(pending, binding)
        decision = _OwnerDecision(self, pending, discord)
        lease = self._lease()
        if self.reminder_config is not None:
            if not isinstance(discord, RepairDiscordApi):
                raise RuntimeError("repair reminder transport lacks a validated binding")
            guild_id = (pending.approval_guild_id or None) if isinstance(pending.approval_guild_id, str) else None
            resolver = channel_guild_resolver(discord.fetch_channel)

            def resolve_guild(channel_id: str) -> str | None:
                nonlocal guild_id
                guild_id = resolver(channel_id)
                return guild_id

            def deliver(channel_id: str, content: str) -> None:
                _ = discord.post_message(channel_id, content)

            context = ReminderContext(
                config=self.reminder_config,
                journal=ReminderJournal(self.store.root / "reminder-journal"),
                request_type=pending.kind or ApprovalKind.REPAIR,
                deliver=deliver,
                clock=self.now,
                guild_id=guild_id,
                guild_id_for=resolve_guild,
                space_for=lambda _channel: stored_reminder_space(pending.surface),
            )
            if reminders is None:
                dispatch_owner_approval_reminder(request, decision, lease, context)
            else:
                reminders.append(ReminderCall(request, decision, lease, context))
        if resolutions is None:
            resolve_owner_decision(request, decision, lease)
        else:
            def resolve() -> None:
                resolve_owner_decision(request, decision, lease)

            resolutions.append(resolve)

    def _discord_for(self, pending: PendingRepairApproval) -> ApprovalPollTransport:
        """Use the record's stored binding when the transport can validate it."""
        if isinstance(self.discord, RepairDiscordApi):
            return self.discord.for_pending(pending)
        return self.discord

    def _expire(self, pending: PendingRepairApproval) -> None:
        with self._lease().hold(repair_approval_key(pending.ticket_id)) as owned:
            if owned:
                self.dispatch(pending, "approval_expired")

    def _lease(self) -> FileKeyLease:
        """Take the SAME key lease the producer takes, so a 900s apply defers it."""
        return FileKeyLease(lease_root(self.store.root))

    def dispatch(self, pending: PendingRepairApproval, outcome: str) -> None:
        """Run one terminal command and retire the record only after it succeeded."""
        if not self.store.claim(pending.ticket_id):
            return
        succeeded = False
        try:
            if outcome == "approved":
                succeeded = self.commands.apply(pending)
            else:
                succeeded = self.commands.discard(pending, outcome)
        finally:
            if succeeded:
                self.store.remove(pending.ticket_id)
                self._record(pending, outcome)
            self.store.release(pending.ticket_id)

    def _record(self, pending: PendingRepairApproval, outcome: str) -> None:
        self.approval_log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _ = self.approval_log.parent.chmod(0o700)
        payload = {
            "action": "repair.approval",
            "approval": {
                "channel": _surface_instruction(pending),
                "message_id": pending.message_id,
                "method": "manual_reaction",
                "owner_id": self.owner_id,
            },
            "hash": pending.action_hash,
            "result": {"status": outcome},
            "target_id": pending.ticket_id,
            "timestamp": self.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with self.approval_log.open("a", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
        _ = self.approval_log.chmod(0o600)


@dataclass(frozen=True, slots=True)
class _OwnerDecision:
    """Bridge one stored repair record onto the shared decision resolver."""

    watcher: RepairApprovalWatcher
    pending: PendingRepairApproval
    discord: ApprovalPollTransport

    def probe(self, request: ApprovalRequest) -> Probe:
        del request
        return probe_pending(self.pending, self.watcher.owner_id, self.discord)

    def apply(self, request: ApprovalRequest, decision: Probe) -> None:
        del request
        match decision:
            case Probe.APPROVED:
                self.watcher.dispatch(self.pending, "approved")
            case Probe.CANCELLED:
                self.watcher.dispatch(self.pending, "owner_cancelled")
            case Probe.BOUND_PENDING | Probe.MISSING | Probe.BINDING_MISMATCH | Probe.UNVERIFIABLE:
                return
            case unreachable:
                assert_never(unreachable)

    def drop(self, request: ApprovalRequest) -> None:
        del request  # dispatch retires the record only after its command succeeded


def _surface_instruction(pending: PendingRepairApproval) -> str:
    kind = pending.kind or ApprovalKind.REPAIR
    surface = pending.surface or required_surface(kind)
    return reaction_instruction(kind, surface, name_surface=True)


def main() -> int:
    """Run one cron tick using only the ops credential that the wrapper resolved."""
    from automation.repair.repair_ops_discord import configured_discord

    discord = configured_discord()
    root = Path(os.environ.get("REPAIR_APPROVAL_PENDING_ROOT", "/srv/autophagy-private/repair-approval-pending"))
    approval_log = Path(os.environ.get("REPAIR_APPROVAL_LOG", "/srv/autophagy-private/repair-approvals.jsonl"))
    cli = Path(os.environ.get("REPAIR_OPS_CLI", "/srv/autophagy-agents/automation/repair/repair_ops_cli.py"))
    RepairApprovalWatcher(
        PendingRepairApprovalStore(root),
        discord,
        CliRepairApprovalCommands(cli, discord.token),
        discord.owner_id,
        approval_log,
        lambda: datetime.now(UTC),
        load_approval_reminder_config(),
    ).run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

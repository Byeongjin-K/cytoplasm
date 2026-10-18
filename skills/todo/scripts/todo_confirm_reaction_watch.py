#!/usr/bin/env python3
from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol


_LIVE_SCRIPTS: Final = "/srv/autophagy-skills/live/todo/scripts"
_SCRIPTS = Path(os.environ.get("TODO_SCRIPTS", _LIVE_SCRIPTS)).expanduser()
if _SCRIPTS.is_dir() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from todo_approval import (  # noqa: E402
    DirectoryLike,
    TodoApprovalGate,
    TodoApprovalIntent,
    _repo_module,
    lifecycle,
    surface_module,
)
from todo_approval_store import (  # noqa: E402
    ApprovalState,
    TodoApprovalRecord,
    TodoApprovalStore,
    approval_ttl,
)
from todo_execution_reconcile import (  # noqa: E402
    RecordApproval,
    append_manual_approval,
    build_runtime,
    execute_approved_writes,
)

if TYPE_CHECKING:
    from automation.interop.approval_lease import ApprovalLease
    from automation.interop.approval_lifecycle import ApprovalRequest, Probe


_ENV_SECRETS: Final = Path.home() / ".env.secrets"


class TodoWatchError(RuntimeError):
    pass


class ReactionTransport(Protocol):
    def post_message(self, channel_id: str, content: str) -> str: ...

    def get_message(self, channel_id: str, message_id: str) -> str | None: ...

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]: ...


@dataclass(slots=True)
class TodoOwnerDecision:
    record: TodoApprovalRecord
    gate: TodoApprovalGate
    store: TodoApprovalStore
    approval_log: Path
    owner_id: str
    now: datetime
    record_approval: RecordApproval
    outcome: str | None = None
    notify: Callable[[TodoApprovalRecord], None] | None = None

    def probe(self, request: ApprovalRequest) -> Probe:
        return self.gate.probe(request)

    def apply(self, request: ApprovalRequest, decision: Probe) -> None:
        del request
        probe = lifecycle().Probe
        if decision is probe.APPROVED:
            self.record_approval(self.approval_log, self.record, self.owner_id, self.now)
            self.outcome = "approved"
        elif decision is probe.CANCELLED:
            self.outcome = "cancelled"
        else:
            raise TodoWatchError("todo owner decision is not terminal")

    def drop(self, request: ApprovalRequest) -> None:
        if self.outcome not in {"approved", "cancelled"}:
            raise TodoWatchError("todo owner decision has no terminal outcome")
        current = self.store.active(request.key)
        if current != self.record:
            raise TodoWatchError("todo pending generation changed before decision archive")
        self.store.archive(self.record, ApprovalState.ARCHIVED, self.outcome)
        if self.outcome == "cancelled" and self.notify is not None:
            try:
                self.notify(self.record)
            except Exception as error:  # noqa: BLE001 — notice must never undo the archive
                print(
                    f"NOTIFY-FAIL key={self.record.key} err={type(error).__name__}",
                    file=sys.stderr,
                )


def run_once(
    *,
    store: TodoApprovalStore,
    owner_id: str,
    transport: ReactionTransport,
    directory: DirectoryLike,
    approval_log: Path,
    lease: ApprovalLease,
    now: datetime,
    record_approval: RecordApproval = append_manual_approval,
    reminder_config: object | None = None,
) -> None:
    moment = now.astimezone(UTC)
    try:
        records = store.all_outstanding()
        reminders = []
        resolutions = []
        for record in records:
            if moment - record.created_at >= approval_ttl():
                _expire(store, lease, record)
                continue
            request = _request(record)
            runtime = build_runtime(store, transport, directory, owner_id, lease, record, moment)
            gate = TodoApprovalGate(_intent(record), runtime)
            decision = TodoOwnerDecision(
                record,
                gate,
                store,
                approval_log,
                owner_id,
                moment,
                record_approval,
                notify=lambda cancelled: _notify_cancelled(cancelled, transport),
            )
            if reminder_config is not None:
                reminder = _repo_module("approval_reminder")
                surface = surface_module()
                context = reminder.ReminderContext(
                    config=reminder_config,
                    journal=_repo_module("approval_lease").ReminderJournal(
                        store.root / "reminder-journal"
                    ),
                    request_type=surface.ApprovalKind(record.kind),
                    deliver=lambda channel_id, content: transport.post_message(
                        channel_id, content
                    ),
                    clock=lambda: moment,
                    guild_id=(record.approval_guild_id or None)
                    if isinstance(record.approval_guild_id, str) else None,
                    guild_id_for=reminder.channel_guild_resolver(transport.fetch_channel),
                    space_for=lambda _channel, surface=record.surface: reminder.stored_reminder_space(surface),
                )
                reminders.append(reminder.ReminderCall(request, decision, lease, context))
            resolutions.append((request, decision))
        if reminders:
            reminder.dispatch_owner_approval_reminders(reminders)
        for request, decision in resolutions:
            lifecycle().resolve_owner_decision(request, decision, lease)
        execute_approved_writes(
            store=store, approval_log=approval_log, owner_id=owner_id, now=moment, lease=lease
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise TodoWatchError("todo approval watcher failed closed") from error


def _expire(store: TodoApprovalStore, lease: ApprovalLease, record: TodoApprovalRecord) -> None:
    with lease.hold(record.key) as owned:
        if not owned:
            return
        current = store.active(record.key)
        if current != record:
            return
        store.archive(record, ApprovalState.EXPIRED, None)
        try:
            importlib.import_module("todo_approval_runtime").notify_expired(record)
        except Exception as error:  # noqa: BLE001 — 통지 실패가 만료 archive를 되돌려서는 안 된다
            print(f"NOTIFY-FAIL key={record.key} err={type(error).__name__}", file=sys.stderr)


def _request(record: TodoApprovalRecord) -> ApprovalRequest:
    if record.message_id is None:
        raise TodoWatchError("todo pending generation has no message binding")
    return lifecycle().ApprovalRequest(
        record.key,
        record.action_hash,
        record.message_id,
        record.channel_id,
        record.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _intent(record: TodoApprovalRecord) -> TodoApprovalIntent:
    return TodoApprovalIntent(
        record.action_hash,
        record.target_id,
        record.argv_summary,
        record.title,
        record.due,
        origin_channel_id=record.origin_channel_id,
        origin_message_id=record.origin_message_id,
        tasklist=record.tasklist,
        notes=record.notes,
    )


def _notify_cancelled(
    record: TodoApprovalRecord, transport: object, *, transport_factory: object | None = None
) -> None:
    """⛔ notice for the archived generation — the request thread, then origin, then fallback."""
    runtime = importlib.import_module("todo_approval_runtime")
    runtime.notify_result(
        {
            "id": record.action_hash[:19],
            "channel_id": record.channel_id,
            "origin_channel_id": record.origin_channel_id,
            "origin_message_id": record.origin_message_id,
            "approval_thread_id": record.approval_thread_id,
            "approval_guild_id": record.approval_guild_id or "",
            "message_id": record.message_id or "",
        },
        f"⛔ 할일 등록 취소 (승인 {record.action_hash[:19]}) — 소유자 ⛔ 리액션으로 취소되어 "
        "Google Tasks에 등록되지 않았습니다.",
        thread_name="할일 등록",
        transport=transport,
        transport_factory=transport_factory,
        outcome=runtime.OUTCOME_CANCELLED,
    )


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main() -> int:
    _repo_module("approval_directory")
    from automation.notice_bot import resolve_token

    try:
        _load_env_secrets()
        token = resolve_token()
        if not token:
            raise TodoWatchError("todo Discord identity is unavailable")
        todo = __import__("todo_cli")
        owner_id = todo.owner_id()
        root = Path(os.environ.get("TODO_APPROVAL_ROOT", "~/.hermes/todo-approvals")).expanduser()
        transport = __import__("todo_discord").TodoDiscordTransport(token)
        directory_type = _repo_module("approval_directory").DiscordChannelDirectory
        directory = directory_type(token, owner_id, transport.api, root / "approval-directory.json")
        lease = _repo_module("approval_lease").FileKeyLease(root / "approval-leases")
        config = _repo_module(
            "approval_reminder_config"
        ).load_approval_reminder_config()
        run_once(
            store=TodoApprovalStore(root),
            owner_id=owner_id,
            transport=transport,
            directory=directory,
            approval_log=todo.approval_log(),
            lease=lease,
            now=datetime.now(UTC),
            reminder_config=config,
        )
    except Exception as error:  # noqa: BLE001 - final no-agent cron boundary
        print(f"todo-confirm-watch error: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

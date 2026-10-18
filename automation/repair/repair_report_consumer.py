"""Agent-owned consumer kept as one A2.3-extensible state machine (# noqa: SIZE_OK)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal, TypeAlias, assert_never
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from automation.repair.repair_capability import reconcile_capabilities, verify
from automation.interop.discord_transport import DISCORD_API
from automation.repair.repair_report_queue import (
    ReportRequest,
    ack_dir,
    line_digest,
    lock_path,
    parse_line,
    queue_dir,
    semantic_key,
)
from automation.repair.repair_report_send import (
    STATUS,
    channel_watermark,
    find_report,
    send_report,
)

_MAX_QUEUE_BYTES: Final = 8 * 1024 * 1024
_MAX_LINE_BYTES: Final = 512
_MAX_PROCESSED: Final = 20
_MAX_POST_ATTEMPTS: Final = 20
_MAX_GET_PAGES: Final = 50
_MAX_PHASE_ATTEMPTS: Final = 10
_GC_AGE: Final = timedelta(days=30)
_KST: Final = ZoneInfo("Asia/Seoul")
COMPLETE_RESULT: Final = "repair lifecycle completed"
REOPEN_REASON: Final = "repair requires owner input"
_BLOCK_EVENT_KINDS: Final = frozenset({"blocked", "dependency_wait", "block_loop_detected"})
_CARD_STATUSES: Final = frozenset(
    {"archived", "blocked", "done", "ready", "review", "running", "scheduled", "todo", "triage"}
)
_STATE_KEYS: Final = frozenset({"records", "reservations", "last_timestamp"})
_RECORD_KEYS: Final = frozenset(
    {
        "ticket_id",
        "operation",
        "reason_code",
        "occurrence",
        "line_digest",
        "semantic_key",
        "transition",
        "report",
        "transition_attempts",
        "report_attempts",
        "reconcile_attempts",
        "terminal_reason",
        "report_timestamp",
        "watermark_message_id",
        "reconcile_cursor",
        "reconcile_upper",
        "message_id_last4",
        "terminal_at",
        "last_warning_at",
    }
)
_TRANSITIONS: Final = frozenset({"pending", "in_flight", "done", "dead"})
_REPORTS: Final = frozenset({"pending", "in_flight", "done", "dead", "paused"})
_TERMINAL_REASONS: Final = frozenset(
    {
        "",
        "ok",
        "malformed",
        "unknown_ticket",
        "bad_capability",
        "duplicate_semantic",
        "request_id_conflict",
        "transition_exhausted",
    }
)

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
Operation: TypeAlias = Literal["complete", "reopen"]

_OPERATIONS: Final[dict[str, Operation]] = {"complete": "complete", "reopen": "reopen"}


class ConsumerStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CardState:
    status: str
    blocked_kind: str | None


@dataclass(frozen=True, slots=True)
class TickBudgetExhausted(RuntimeError):
    resource: str

    def __str__(self) -> str:
        return f"repair report tick {self.resource} budget exhausted"


@dataclass(frozen=True, slots=True)
class GetBudgetExhausted(RuntimeError):
    cursor: str

    def __str__(self) -> str:
        return "repair report tick GET budget exhausted"


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class TickBudgets:
    """Mutable per-tick counters shared by all records in one locked consume pass."""

    post_remaining: int = _MAX_POST_ATTEMPTS
    get_remaining: int = _MAX_GET_PAGES
    cache: dict[str, JsonValue] = field(default_factory=dict)

    def post(self) -> None:
        if self.post_remaining <= 0:
            raise TickBudgetExhausted("POST")
        self.post_remaining -= 1

    def fetch(self, path: str) -> JsonValue:
        cached = self.cache.get(path)
        if cached is not None:
            return cached
        if "/messages" in path:
            if self.get_remaining <= 0:
                cursor = path.split("before=", maxsplit=1)[1].split("&", maxsplit=1)[0] if "before=" in path else ""
                raise GetBudgetExhausted(cursor)
            self.get_remaining -= 1
        payload = _discord_fetch(path)
        self.cache[path] = payload
        return payload


@dataclass(frozen=True, slots=True)
class ConsumerRecord:
    """Durable request identity plus transition/report progress."""

    ticket_id: str
    operation: Operation
    reason_code: str
    occurrence: str
    line_digest: str
    semantic_key: str
    transition: str
    report: str
    transition_attempts: int
    report_attempts: int
    reconcile_attempts: int
    terminal_reason: str
    report_timestamp: str
    watermark_message_id: str
    reconcile_cursor: str
    reconcile_upper: str
    message_id_last4: str
    terminal_at: str
    last_warning_at: str


@dataclass(frozen=True, slots=True)
class ConsumerState:
    records: dict[str, ConsumerRecord]
    reservations: dict[str, str]
    last_timestamp: str


def _state_path() -> Path:
    return Path.home() / ".hermes" / "repair-report-consumer" / "state.json"


def _state_lock_path() -> Path:
    return _state_path().with_suffix(".json.lock")


def _json_bytes(payload: JsonValue) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count == 0:
            raise OSError("atomic write made no progress")
        written += count


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_atomic(path: Path, payload: bytes, *, immutable: bool = False) -> bool:
    if immutable and path.exists():
        return False
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    replaced = False
    try:
        os.fchmod(descriptor, 0o640)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if immutable and path.exists():
            return False
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(path.parent)
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not replaced:
            temporary.unlink(missing_ok=True)


def _load_json(path: Path) -> JsonValue:
    return json.loads(path.read_text(encoding="utf-8"))


def _string_field(payload: dict[str, JsonValue], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str):
        raise ConsumerStateError("consumer record string field is invalid")
    return value


def _counter_field(payload: dict[str, JsonValue], field: str) -> int:
    value = payload[field]
    if not isinstance(value, int) or value < 0:
        raise ConsumerStateError("consumer record counter is invalid")
    return value


def _operation_field(payload: dict[str, JsonValue]) -> Operation:
    value = _string_field(payload, "operation")
    operation = _OPERATIONS.get(value)
    if operation is None:
        raise ConsumerStateError("consumer record operation is invalid")
    return operation


def _parse_record(payload: JsonValue) -> ConsumerRecord:
    if not isinstance(payload, dict) or frozenset(payload) != _RECORD_KEYS:
        raise ConsumerStateError("consumer record shape is invalid")
    transition = _string_field(payload, "transition")
    report = _string_field(payload, "report")
    terminal_reason = _string_field(payload, "terminal_reason")
    if transition not in _TRANSITIONS or report not in _REPORTS:
        raise ConsumerStateError("consumer record progress is invalid")
    if terminal_reason not in _TERMINAL_REASONS:
        raise ConsumerStateError("consumer record terminal reason is invalid")
    return ConsumerRecord(
        ticket_id=_string_field(payload, "ticket_id"),
        operation=_operation_field(payload),
        reason_code=_string_field(payload, "reason_code"),
        occurrence=_string_field(payload, "occurrence"),
        line_digest=_string_field(payload, "line_digest"),
        semantic_key=_string_field(payload, "semantic_key"),
        transition=transition,
        report=report,
        transition_attempts=_counter_field(payload, "transition_attempts"),
        report_attempts=_counter_field(payload, "report_attempts"),
        reconcile_attempts=_counter_field(payload, "reconcile_attempts"),
        terminal_reason=terminal_reason,
        report_timestamp=_string_field(payload, "report_timestamp"),
        watermark_message_id=_string_field(payload, "watermark_message_id"),
        reconcile_cursor=_string_field(payload, "reconcile_cursor"),
        reconcile_upper=_string_field(payload, "reconcile_upper"),
        message_id_last4=_string_field(payload, "message_id_last4"),
        terminal_at=_string_field(payload, "terminal_at"),
        last_warning_at=_string_field(payload, "last_warning_at"),
    )


def _load_state() -> ConsumerState:
    path = _state_path()
    if not path.exists():
        return ConsumerState(records={}, reservations={}, last_timestamp="")
    payload = _load_json(path)
    if not isinstance(payload, dict) or frozenset(payload) != _STATE_KEYS:
        raise ConsumerStateError("consumer state shape is invalid")
    raw_records = payload["records"]
    raw_reservations = payload["reservations"]
    last_timestamp = payload["last_timestamp"]
    if not isinstance(raw_records, dict) or not isinstance(raw_reservations, dict):
        raise ConsumerStateError("consumer state maps are invalid")
    if not isinstance(last_timestamp, str):
        raise ConsumerStateError("consumer timestamp is invalid")
    if last_timestamp:
        parsed_timestamp = datetime.fromisoformat(last_timestamp)
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
            raise ConsumerStateError("consumer timestamp lacks a timezone")
    records = {request_id: _parse_record(record) for request_id, record in raw_records.items()}
    reservations: dict[str, str] = {}
    for key, owner in raw_reservations.items():
        if not isinstance(owner, str):
            raise ConsumerStateError("consumer reservation is invalid")
        reservations[key] = owner
    if any(reservations.get(record.semantic_key) != request_id for request_id, record in records.items()):
        raise ConsumerStateError("consumer record lacks its reservation")
    return ConsumerState(records=records, reservations=reservations, last_timestamp=last_timestamp)


def _save_state(state: ConsumerState) -> None:
    records_payload: dict[str, JsonValue] = {
        request_id: _record_payload(record) for request_id, record in state.records.items()
    }
    reservations_payload: dict[str, JsonValue] = dict(state.reservations)
    payload: JsonValue = {
        "records": records_payload,
        "reservations": reservations_payload,
        "last_timestamp": state.last_timestamp,
    }
    _write_atomic(_state_path(), _json_bytes(payload))


def _record_payload(record: ConsumerRecord) -> dict[str, JsonValue]:
    return {
        "ticket_id": record.ticket_id,
        "operation": record.operation,
        "reason_code": record.reason_code,
        "occurrence": record.occurrence,
        "line_digest": record.line_digest,
        "semantic_key": record.semantic_key,
        "transition": record.transition,
        "report": record.report,
        "transition_attempts": record.transition_attempts,
        "report_attempts": record.report_attempts,
        "reconcile_attempts": record.reconcile_attempts,
        "terminal_reason": record.terminal_reason,
        "report_timestamp": record.report_timestamp,
        "watermark_message_id": record.watermark_message_id,
        "reconcile_cursor": record.reconcile_cursor,
        "reconcile_upper": record.reconcile_upper,
        "message_id_last4": record.message_id_last4,
        "terminal_at": record.terminal_at,
        "last_warning_at": record.last_warning_at,
    }


def _load_ticket_allowlist() -> frozenset[str]:
    registry_path = Path(os.environ.get("REPAIR_STATE_FILE", "~/.hermes/repair-tickets.json")).expanduser()
    payload = _load_json(registry_path)
    if not isinstance(payload, dict):
        raise ConsumerStateError("repair registry is invalid")
    tickets: set[str] = set()
    for entry in payload.values():
        if not isinstance(entry, dict):
            raise ConsumerStateError("repair registry entry is invalid")
        ticket_id = entry.get("ticket_id")
        if not isinstance(ticket_id, str):
            raise ConsumerStateError("repair registry ticket is invalid")
        tickets.add(ticket_id)
    return frozenset(tickets)


def _snapshot_lines() -> list[bytes] | None:
    try:
        with lock_path().open("rb") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_SH)
            descriptor = os.open(
                queue_dir() / "pending.jsonl",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            )
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    print("repair report consumer warning: pending queue is not regular", file=sys.stderr)
                    return None
                if metadata.st_size > _MAX_QUEUE_BYTES:
                    print("repair report consumer warning: pending queue exceeds size limit", file=sys.stderr)
                    return None
                remaining = metadata.st_size
                chunks: list[bytes] = []
                while remaining:
                    chunk = os.read(descriptor, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
            finally:
                os.close(descriptor)
    except FileNotFoundError:
        return []
    except OSError:
        print("repair report consumer warning: pending queue unavailable", file=sys.stderr)
        return None
    snapshot = b"".join(chunks)
    if not snapshot.endswith(b"\n"):
        snapshot = snapshot.rsplit(b"\n", maxsplit=1)[0] + b"\n" if b"\n" in snapshot else b""
    if not snapshot:
        return []
    return [segment + b"\n" for segment in snapshot.removesuffix(b"\n").split(b"\n")]


def _terminal_receipt(name: str) -> dict[str, JsonValue] | None:
    try:
        payload = _load_json(ack_dir() / name)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("terminal_at"), str):
        return None
    return payload


def _terminal(record: ConsumerRecord) -> bool:
    return record.transition in {"done", "dead"} and record.report in {"done", "dead"}


def _new_record(request: ReportRequest, digest: str, key: str) -> ConsumerRecord:
    operation = _OPERATIONS.get(request.operation)
    if operation is None:
        raise ConsumerStateError("report request operation is invalid")
    return ConsumerRecord(
        ticket_id=request.ticket_id,
        operation=operation,
        reason_code=request.reason_code,
        occurrence=request.occurrence,
        line_digest=digest,
        semantic_key=key,
        transition="pending",
        report="pending",
        transition_attempts=0,
        report_attempts=0,
        reconcile_attempts=0,
        terminal_reason="",
        report_timestamp="",
        watermark_message_id="",
        reconcile_cursor="",
        reconcile_upper="",
        message_id_last4="",
        terminal_at="",
        last_warning_at="",
    )


def _dead_record(request: ReportRequest, digest: str, key: str, reason: str) -> ConsumerRecord:
    return replace(
        _new_record(request, digest, key),
        transition="dead",
        report="dead",
        terminal_reason=reason,
        terminal_at=datetime.now(tz=UTC).isoformat(),
    )


def _next_timestamp(state: ConsumerState) -> tuple[ConsumerState, str]:
    now = datetime.now(tz=_KST)
    if state.last_timestamp:
        previous = datetime.fromisoformat(state.last_timestamp)
        if now <= previous:
            now = previous + timedelta(microseconds=1)
    timestamp = now.isoformat()
    return replace(state, last_timestamp=timestamp), timestamp


def _child_env() -> dict[str, str]:
    path = os.environ.get("PATH", "")
    return {**os.environ, "PATH": f"{Path.home()}/.local/bin:{path}"}


def _discord_fetch(path: str) -> JsonValue:
    from automation.notice_bot import resolve_token

    request = Request(
        f"{DISCORD_API}{path}",
        headers={
            "Authorization": f"Bot {resolve_token(required=True)}",
            "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        payload: JsonValue = json.loads(response.read().decode("utf-8"))
    return payload


def _run_kanban(*arguments: str) -> None:
    _ = subprocess.run(
        ["hermes", "kanban", *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=_child_env(),
    )


def card_state(ticket_id: str) -> CardState:
    completed = subprocess.run(
        ["hermes", "kanban", "show", ticket_id, "--json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=_child_env(),
    )
    payload: JsonValue = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise ConsumerStateError("kanban show response is invalid")
    task = payload.get("task")
    events = payload.get("events")
    if not isinstance(task, dict) or not isinstance(events, list):
        raise ConsumerStateError("kanban show response shape is invalid")
    status = task.get("status")
    if not isinstance(status, str) or status not in _CARD_STATUSES:
        raise ConsumerStateError("kanban card status is invalid")
    blocked_kind: str | None = None
    if status == "blocked":
        for event in events:
            if not isinstance(event, dict) or event.get("kind") not in _BLOCK_EVENT_KINDS:
                continue
            event_payload = event.get("payload")
            if isinstance(event_payload, dict):
                kind = event_payload.get("kind")
                blocked_kind = kind if isinstance(kind, str) else None
    return CardState(status=status, blocked_kind=blocked_kind)


def _store_record(state: ConsumerState, request_id: str, record: ConsumerRecord) -> ConsumerState:
    updated = replace(state, records=state.records | {request_id: record})
    _save_state(updated)
    return updated


def _request_copy(record: ConsumerRecord) -> ReportRequest:
    return ReportRequest(
        request_id="0" * 32,
        operation=record.operation,
        ticket_id=record.ticket_id,
        reason_code=record.reason_code,
        occurrence=record.occurrence,
        mac="0" * 64,
        created=record.report_timestamp,
    )


def _transition_target(record: ConsumerRecord, state: CardState) -> bool:
    match record.operation:
        case "complete":
            return state.status == "done"
        case "reopen":
            return state.status == "blocked" and state.blocked_kind == "needs_input"
        case _ as unreachable:
            assert_never(unreachable)


def _transition_commands(record: ConsumerRecord, state: CardState) -> tuple[tuple[str, ...], ...]:
    match record.operation:
        case "complete":
            return (("complete", record.ticket_id, "--result", COMPLETE_RESULT),)
        case "reopen" if state.status == "blocked":
            return (
                ("unblock", record.ticket_id),
                ("block", "--kind", "needs_input", record.ticket_id, REOPEN_REASON),
            )
        case "reopen":
            return (("block", "--kind", "needs_input", record.ticket_id, REOPEN_REASON),)
        case _ as unreachable:
            assert_never(unreachable)


def _execute_transition(state: ConsumerState, request_id: str) -> ConsumerState:
    record = state.records[request_id]
    current = card_state(record.ticket_id)
    if _transition_target(record, current):
        return _store_record(state, request_id, replace(record, transition="done"))
    in_flight = replace(record, transition="in_flight")
    state = _store_record(state, request_id, in_flight)
    try:
        for command in _transition_commands(in_flight, current):
            _run_kanban(*command)
    except (OSError, subprocess.SubprocessError):
        return state
    confirmed = card_state(record.ticket_id)
    if _transition_target(record, confirmed):
        return _store_record(state, request_id, replace(in_flight, transition="done"))
    attempts = record.transition_attempts + 1
    if attempts > _MAX_PHASE_ATTEMPTS:
        dead = replace(
            in_flight,
            transition="dead",
            report="dead",
            transition_attempts=attempts,
            terminal_reason="transition_exhausted",
            terminal_at=datetime.now(tz=UTC).isoformat(),
        )
        return _store_record(state, request_id, dead)
    pending = replace(in_flight, transition="pending", transition_attempts=attempts)
    return _store_record(state, request_id, pending)


def _warn_paused(state: ConsumerState, request_id: str) -> ConsumerState:
    record = state.records[request_id]
    now = datetime.now(tz=_KST)
    if record.last_warning_at:
        warned = datetime.fromisoformat(record.last_warning_at)
        if warned.date() == now.date():
            return state
    print("repair report consumer warning: confirmation paused", file=sys.stderr)
    return _store_record(state, request_id, replace(record, last_warning_at=now.isoformat()))


def _pause_report(state: ConsumerState, request_id: str) -> ConsumerState:
    record = state.records[request_id]
    state = _store_record(state, request_id, replace(record, report="paused"))
    return _warn_paused(state, request_id)


def _send_in_flight(
    state: ConsumerState,
    request_id: str,
    budgets: TickBudgets,
) -> ConsumerState:
    record = state.records[request_id]
    try:
        suffix = send_report(
            _request_copy(record),
            datetime.fromisoformat(record.report_timestamp),
            budget=budgets.post,
        )
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        return state
    done = replace(
        record,
        report="done",
        terminal_reason="ok",
        message_id_last4=suffix,
        terminal_at=datetime.now(tz=UTC).isoformat(),
    )
    return _store_record(state, request_id, done)


def _start_report(state: ConsumerState, request_id: str, budgets: TickBudgets) -> ConsumerState:
    record = state.records[request_id]
    if record.report_attempts > _MAX_PHASE_ATTEMPTS:
        return _pause_report(state, request_id)
    try:
        watermark = channel_watermark(fetcher=budgets.fetch)
    except GetBudgetExhausted:
        return state
    state, timestamp = _next_timestamp(state)
    in_flight = replace(
        record,
        report="in_flight",
        report_timestamp=timestamp,
        watermark_message_id=watermark,
        reconcile_upper="",
        reconcile_cursor="",
    )
    state = _store_record(state, request_id, in_flight)
    return _send_in_flight(state, request_id, budgets)


def _execute_transition_and_report(
    state: ConsumerState,
    request_id: str,
    budgets: TickBudgets,
) -> ConsumerState:
    record = state.records[request_id]
    if record.transition not in {"done", "dead"}:
        state = _execute_transition(state, request_id)
        record = state.records[request_id]
    if record.transition == "done" and record.report == "pending":
        return _start_report(state, request_id, budgets)
    return state


def _reconcile_failure(state: ConsumerState, request_id: str) -> ConsumerState:
    record = state.records[request_id]
    attempts = record.reconcile_attempts + 1
    state = _store_record(state, request_id, replace(record, reconcile_attempts=attempts))
    return _pause_report(state, request_id) if attempts > _MAX_PHASE_ATTEMPTS else state


def _reconcile_record(
    state: ConsumerState,
    request_id: str,
    budgets: TickBudgets,
) -> ConsumerState:
    record = state.records[request_id]
    if record.report_attempts > _MAX_PHASE_ATTEMPTS and record.report != "paused":
        return _pause_report(state, request_id)
    if budgets.get_remaining <= 0:
        return state
    try:
        if not record.reconcile_upper:
            upper = channel_watermark(fetcher=budgets.fetch)
            record = replace(record, reconcile_upper=upper, reconcile_cursor="")
            state = _store_record(state, request_id, record)
        found, cursor, exhausted = find_report(
            task_id=record.ticket_id,
            status=STATUS[record.operation],
            timestamp_iso=record.report_timestamp,
            upper=record.reconcile_upper,
            lower=record.watermark_message_id,
            cursor=record.reconcile_cursor or None,
            fetcher=budgets.fetch,
        )
    except GetBudgetExhausted as error:
        if error.cursor and error.cursor != record.reconcile_cursor:
            return _store_record(state, request_id, replace(record, reconcile_cursor=error.cursor))
        return state
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        return _reconcile_failure(state, request_id)
    if found:
        done = replace(
            record,
            report="done",
            terminal_reason="ok",
            reconcile_cursor=cursor,
            terminal_at=datetime.now(tz=UTC).isoformat(),
        )
        return _store_record(state, request_id, done)
    if not exhausted:
        return _store_record(state, request_id, replace(record, reconcile_cursor=cursor))
    attempts = record.report_attempts + 1
    if attempts > _MAX_PHASE_ATTEMPTS:
        state = _store_record(state, request_id, replace(record, report_attempts=attempts))
        return _pause_report(state, request_id)
    try:
        watermark = channel_watermark(fetcher=budgets.fetch)
    except GetBudgetExhausted:
        return state
    state, timestamp = _next_timestamp(state)
    resend = replace(
        record,
        report="in_flight",
        report_attempts=attempts,
        report_timestamp=timestamp,
        watermark_message_id=watermark,
        reconcile_upper="",
        reconcile_cursor="",
    )
    state = _store_record(state, request_id, resend)
    return _send_in_flight(state, request_id, budgets)


def _reconcile_start_records(
    state: ConsumerState,
    budgets: TickBudgets,
) -> tuple[ConsumerState, int, frozenset[str]]:
    request_ids = frozenset(
        request_id for request_id, record in state.records.items() if record.report in {"in_flight", "paused"}
    )
    completed = 0
    current = state
    for request_id in request_ids:
        if budgets.get_remaining <= 0:
            break
        current = _reconcile_record(current, request_id, budgets)
        record = current.records[request_id]
        if _terminal(record):
            _finish_record(request_id, record)
            completed += 1
    return current, completed, request_ids


def _ack_payload(request_id: str, record: ConsumerRecord) -> bytes:
    payload: JsonValue = {"request_id": request_id, **_record_payload(record)}
    return _json_bytes(payload)


def _finish_record(request_id: str, record: ConsumerRecord) -> None:
    if not _terminal(record):
        return
    if record.report == "done":
        report_key = hashlib.sha256(
            f"{record.ticket_id}|{record.operation}|{record.reason_code}".encode()
        ).hexdigest()
        marker: JsonValue = {
            "ticket_id": record.ticket_id,
            "operation": record.operation,
            "reason_code": record.reason_code,
            "first_reported_at": record.report_timestamp,
        }
        _write_atomic(ack_dir() / f"reported-{report_key}.json", _json_bytes(marker), immutable=True)
    payload = _ack_payload(request_id, record)
    _write_atomic(ack_dir() / f"sem-{record.semantic_key}.json", payload, immutable=True)
    _write_atomic(ack_dir() / f"{request_id}.json", payload, immutable=True)


def _write_dead_ack(request: ReportRequest, digest: str, key: str, reason: str) -> None:
    record = _dead_record(request, digest, key, reason)
    _write_atomic(ack_dir() / f"{request.request_id}.json", _ack_payload(request.request_id, record), immutable=True)


def _write_line_receipt(prefix: str, digest: str, reason: str) -> None:
    payload: JsonValue = {
        "line_digest": digest,
        "terminal_reason": reason,
        "terminal_at": datetime.now(tz=UTC).isoformat(),
    }
    _write_atomic(ack_dir() / f"{prefix}-{digest}.json", _json_bytes(payload), immutable=True)


def _identity_conflicts(receipt: dict[str, JsonValue], digest: str, key: str) -> bool:
    return receipt.get("line_digest") != digest or receipt.get("semantic_key") != key


def _resume_records(
    state: ConsumerState,
    remaining: int,
    queued_request_ids: frozenset[str],
    reconciled_request_ids: frozenset[str],
    budgets: TickBudgets,
) -> tuple[ConsumerState, int, int]:
    completed = 0
    processed = 0
    current = state
    for request_id, stored in state.records.items():
        if (
            processed >= remaining
            or request_id in queued_request_ids
            or request_id in reconciled_request_ids
        ):
            continue
        ack = _terminal_receipt(f"{request_id}.json")
        semantic = _terminal_receipt(f"sem-{stored.semantic_key}.json")
        if ack is not None and semantic is not None:
            continue
        record = stored
        if not _terminal(record):
            current = _execute_transition_and_report(current, request_id, budgets)
            record = current.records[request_id]
            processed += 1
        if _terminal(record):
            _finish_record(request_id, record)
            completed += 1
            if stored == record:
                processed += 1
    return current, completed, processed


def _gc_state(state: ConsumerState) -> ConsumerState:
    cutoff = datetime.now(tz=UTC) - _GC_AGE
    retained: dict[str, ConsumerRecord] = {}
    reservations = dict(state.reservations)
    for request_id, record in state.records.items():
        eligible = False
        if record.report != "paused" and record.terminal_at:
            terminal_at = datetime.fromisoformat(record.terminal_at)
            eligible = (
                terminal_at < cutoff
                and _terminal_receipt(f"{request_id}.json") is not None
                and _terminal_receipt(f"sem-{record.semantic_key}.json") is not None
            )
        if eligible:
            reservations.pop(record.semantic_key, None)
        else:
            retained[request_id] = record
    return replace(state, records=retained, reservations=reservations)


def _consume_locked() -> int:
    try:
        _ = reconcile_capabilities()
    except Exception as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        print(f"repair capability reconcile skipped: {error.__class__.__name__}", file=sys.stderr)
    state = _load_state()
    allowlist = _load_ticket_allowlist()
    budgets = TickBudgets()
    state, completed, reconciled_request_ids = _reconcile_start_records(state, budgets)
    lines = _snapshot_lines()
    if lines is None:
        return 0
    queued_request_ids = frozenset(
        request.request_id
        for raw in lines
        if (request := parse_line(raw)) is not None
    )
    state, resumed, processed = _resume_records(
        state,
        _MAX_PROCESSED,
        queued_request_ids,
        reconciled_request_ids,
        budgets,
    )
    completed += resumed
    for raw in lines:
        if processed >= _MAX_PROCESSED:
            break
        digest = line_digest(raw)
        if len(raw.removesuffix(b"\n")) > _MAX_LINE_BYTES:
            _write_line_receipt("invalid", digest, "malformed")
            completed += 1
            processed += 1
            continue
        request = parse_line(raw)
        if request is None:
            _write_line_receipt("invalid", digest, "malformed")
            completed += 1
            processed += 1
            continue
        key = semantic_key(request)
        stored = state.records.get(request.request_id)
        ack = _terminal_receipt(f"{request.request_id}.json")
        if (stored is not None and (stored.line_digest != digest or stored.semantic_key != key)) or (
            ack is not None and _identity_conflicts(ack, digest, key)
        ):
            _write_line_receipt("conflict", digest, "request_id_conflict")
            completed += 1
            processed += 1
            continue
        if ack is not None:
            continue
        if _terminal_receipt(f"sem-{key}.json") is not None:
            _write_dead_ack(request, digest, key, "duplicate_semantic")
            completed += 1
            processed += 1
            continue
        if request.ticket_id not in allowlist:
            _write_dead_ack(request, digest, key, "unknown_ticket")
            completed += 1
            processed += 1
            continue
        if not verify(request.ticket_id, request.occurrence, request.mac):
            _write_dead_ack(request, digest, key, "bad_capability")
            completed += 1
            processed += 1
            continue
        owner = state.reservations.get(key)
        if owner is not None and owner != request.request_id:
            continue
        if stored is not None:
            if request.request_id in reconciled_request_ids:
                continue
            record = stored
            if not _terminal(record):
                state = _execute_transition_and_report(state, request.request_id, budgets)
                record = state.records[request.request_id]
                processed += 1
            if _terminal(record):
                _finish_record(request.request_id, record)
                completed += 1
                if stored == record:
                    processed += 1
            continue
        record = _new_record(request, digest, key)
        state = replace(
            state,
            records=state.records | {request.request_id: record},
            reservations=state.reservations | {key: request.request_id},
        )
        _save_state(state)
        state = _execute_transition_and_report(state, request.request_id, budgets)
        record = state.records[request.request_id]
        processed += 1
        if _terminal(record):
            _finish_record(request.request_id, record)
            completed += 1
    collected = _gc_state(state)
    if collected != state:
        _save_state(collected)
    return completed


def consume_once() -> int:  # noqa: C901
    """Consume one bounded tick while holding the agent-owned state lock."""
    lock = _state_lock_path()
    try:
        lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock.parent.chmod(0o700)
        with lock.open("a+b") as lock_handle:
            os.fchmod(lock_handle.fileno(), 0o640)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            return _consume_locked()
    except Exception as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        print(f"repair report consumer failed: {error.__class__.__name__}", file=sys.stderr)
        return 0

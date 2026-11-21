"""Deduplicated repair tickets with a strictly redacted Kanban boundary."""

from __future__ import annotations

import fcntl
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from automation.credential_rotation.files import atomic_write
from automation.repair.repair_redaction import digest, excerpt


class RepairStateError(RuntimeError):
    """Raised when the durable deduplication state cannot be parsed safely."""


class KanbanPort(Protocol):
    """Minimal Kanban mutation surface that never accepts raw logs."""

    def create(self, title: str, body: str, idempotency_key: str) -> str: ...

    def comment(self, ticket_id: str, text: str) -> None: ...

    def block_for_repair(self, ticket_id: str, reason: str) -> None: ...


class PrivateLogPort(Protocol):
    """Ops bridge that accepts raw logs only over the restricted channel."""

    def write(self, ticket_id: str, occurrence: int, raw_log: str) -> str: ...


@dataclass(frozen=True, slots=True)
class RepairEvent:
    source: str
    location: str
    raw_log: str


@dataclass(frozen=True, slots=True)
class RepairResult:
    ticket_id: str
    occurrence: int
    created: bool
    excerpt: str
    log_hash: str
    private_path: str
    signature: str


@dataclass(frozen=True, slots=True)
class RepairClaim:
    ticket_id: str
    occurrence: int
    created: bool


class RepairRegistry:
    """Agent-owned metadata store; it keeps hashes and counts but no log text."""

    def __init__(self, state_file: Path) -> None:
        self._state_file = state_file
        self._lock_file = state_file.with_suffix(".lock")

    def claim(
        self,
        signature: str,
        create_ticket: Callable[[str | None], str],
        is_ticket_closed: Callable[[str], bool | None] | None = None,
    ) -> RepairClaim:
        """Reserve one occurrence under an exclusive lock and create at most one card."""
        self._state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._lock_file.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            tickets = self._load()
            current = tickets.get(signature)
            if current is None:
                ticket_id = create_ticket(None)
                tickets[signature] = {"ticket_id": ticket_id, "occurrences": 1}
                self._save(tickets)
                return RepairClaim(ticket_id=ticket_id, occurrence=1, created=True)
            ticket_id = current["ticket_id"]
            stored_occurrence = current["occurrences"]
            if not isinstance(ticket_id, str) or not isinstance(stored_occurrence, int):
                raise RepairStateError("repair state entry is malformed")
            try:
                is_closed = None if is_ticket_closed is None else is_ticket_closed(ticket_id)
            except Exception:  # a board we cannot read must not mint duplicate cards
                is_closed = None
            if is_closed is True:
                superseded = ticket_id
                ticket_id = create_ticket(superseded)
                tickets[signature] = {"ticket_id": ticket_id, "occurrences": 1}
                self._save(tickets)
                return RepairClaim(ticket_id=ticket_id, occurrence=1, created=True)
            occurrence = stored_occurrence + 1
            tickets[signature] = {"ticket_id": ticket_id, "occurrences": occurrence}
            self._save(tickets)
            return RepairClaim(ticket_id=ticket_id, occurrence=occurrence, created=False)

    def _load(self) -> dict[str, dict[str, str | int]]:
        try:
            raw = self._state_file.read_bytes()
        except FileNotFoundError:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            # Fail closed instead of starting over: a forgotten signature re-creates its card with the
            # first dedup key, which the board answers with the old card even when it is done.
            raise RepairStateError(f"repair state is not valid JSON ({len(raw)} bytes)") from error
        if not isinstance(payload, dict):
            raise RepairStateError("repair state is not an object")
        parsed: dict[str, dict[str, str | int]] = {}
        for signature, entry in payload.items():
            if not isinstance(signature, str) or not isinstance(entry, dict):
                raise RepairStateError("repair state entry is malformed")
            ticket_id, occurrences = entry.get("ticket_id"), entry.get("occurrences")
            if not isinstance(ticket_id, str) or not isinstance(occurrences, int) or occurrences < 1:
                raise RepairStateError("repair state fields are malformed")
            parsed[signature] = {"ticket_id": ticket_id, "occurrences": occurrences}
        return parsed

    def _save(self, tickets: dict[str, dict[str, str | int]]) -> None:
        # A full disk must leave the previous registry, not a truncated one (2026-11-19: 0 bytes).
        atomic_write(self._state_file, (json.dumps(tickets, sort_keys=True) + "\n").encode("utf-8"))


class RepairService:
    """Convert one raw failure into private storage plus a redacted repair card."""

    def __init__(
        self,
        kanban: KanbanPort,
        private_logs: PrivateLogPort,
        registry: RepairRegistry,
        is_ticket_closed: Callable[[str], bool | None] | None = None,
    ) -> None:
        self._kanban = kanban
        self._private_logs = private_logs
        self._registry = registry
        self._is_ticket_closed = is_ticket_closed

    def record(self, event: RepairEvent) -> RepairResult:
        """Create or update a blocked ticket without sending the raw log to Kanban."""
        safe_excerpt = excerpt(event.raw_log)
        signature = digest(f"{event.source}\n{event.location}\n{_error_class(safe_excerpt)}")
        claim = self._registry.claim(
            signature,
            lambda superseded: self._create_ticket(event, signature, safe_excerpt, superseded),
            self._is_ticket_closed,
        )
        private_path = self._private_logs.write(claim.ticket_id, claim.occurrence, event.raw_log)
        log_hash = digest(event.raw_log)
        self._kanban.comment(
            claim.ticket_id,
            _comment(claim.occurrence, safe_excerpt, log_hash, private_path),
        )
        return RepairResult(
            ticket_id=claim.ticket_id,
            occurrence=claim.occurrence,
            created=claim.created,
            excerpt=safe_excerpt,
            log_hash=log_hash,
            private_path=private_path,
            signature=signature,
        )

    def _create_ticket(
        self, event: RepairEvent, signature: str, safe_excerpt: str, superseded: str | None
    ) -> str:
        body = f"Repair ticket. Redacted excerpt: {safe_excerpt}\nSignature: {signature}"
        idempotency_key = f"repair-{signature}"
        if superseded is not None:
            # The board returns the existing non-archived task for a repeated dedup key, so a
            # superseding card must ask for a key the closed card never owned; reusing it would
            # hand the recurrence straight back to the card it is meant to replace.
            body = f"{body}\nSupersedes closed card: {superseded}"
            idempotency_key = f"{idempotency_key}-after-{superseded}"
        ticket_id = self._kanban.create(
            title=f"수리: {event.source}",
            body=body,
            idempotency_key=idempotency_key,
        )
        self._kanban.block_for_repair(ticket_id, "Needs human repair review; do not dispatch an LLM worker.")
        return ticket_id


def _error_class(safe_excerpt: str) -> str:
    """Use only the leading error token to keep the dedup key stable and non-secret."""
    return safe_excerpt.split(maxsplit=1)[0] if safe_excerpt else "empty-error"


def _comment(occurrence: int, safe_excerpt: str, log_hash: str, private_path: str) -> str:
    return (
        f"Repair occurrence: {occurrence}\nRedacted excerpt: {safe_excerpt}\n"
        f"sha256={log_hash}\nprivate-log={private_path}"
    )

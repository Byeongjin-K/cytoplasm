"""Proposal records: one frozen proposal per revision, bound to its approval card.

A revision is a new record under the same approval key, so the shared lifecycle
supersedes the still-pending card of the previous revision (delete, then drop) and posts
the new one in the same request thread. A record the owner already decided is never
revised — an approved proposal is applied as approved, and a change after that is a new
proposal. The frozen proposal JSON is re-hashed before posting and before applying, so
bytes edited on disk after the request cannot be approved or applied.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import secrets
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Final

from .models import Proposal, ProjectBoardError, canonical_json, proposal_from_dict, proposal_hash

LIVE_STATUSES: Final = frozenset({"proposed", "posted", "approved"})
REVISABLE_STATUSES: Final = frozenset({"proposed", "posted", "cancelled", "abandoned", "superseded"})


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    request_id: str
    key: str
    action_hash: str
    project: str
    todo_count: int
    source_kind: str
    source_id: str
    source_subject: str
    created_at: str
    revision: int = 1
    previous_request_id: str = ""
    status: str = "proposed"
    kind: str = ""
    surface: str = ""
    channel_id: str = ""
    policy_version: int = 0
    message_id: str | None = None
    approval_thread_id: str = ""
    approval_guild_id: str = ""
    origin_channel_id: str = ""
    origin_message_id: str = ""
    render_version: str | None = None
    applied_todos: int = 0
    apply_attempts: int = 0
    last_error: str = ""
    finished_at: str = ""


def approval_key(kind: str, source_id: str) -> str:
    return "project-board:" + hashlib.sha256(f"{kind}:{source_id}".encode()).hexdigest()[:24]


def new_record(
    proposal: Proposal,
    *,
    created_at: str,
    revision: int = 1,
    previous_request_id: str = "",
    origin_channel_id: str = "",
    origin_message_id: str = "",
) -> ProposalRecord:
    source = proposal.source
    return ProposalRecord(
        request_id="pb-" + secrets.token_hex(6),
        key=approval_key(source.kind, source.source_id),
        action_hash=proposal_hash(proposal),
        project=proposal.project,
        todo_count=len(proposal.todos),
        source_kind=source.kind,
        source_id=source.source_id,
        source_subject=source.subject,
        created_at=created_at,
        revision=revision,
        previous_request_id=previous_request_id,
        origin_channel_id=origin_channel_id,
        origin_message_id=origin_message_id,
    )


class ProposalStore:
    """``proposals.json`` + ``frozen/<request_id>.json`` under one flock; writes are atomic 0600."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def _state(self) -> Path:
        return self.root / "proposals.json"

    def frozen_path(self, request_id: str) -> Path:
        return self.root / "frozen" / f"{request_id}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.root / "proposals.lock").open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, ProposalRecord]:
        try:
            payload = json.loads(self._state.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise ProjectBoardError("proposal store is unreadable") from error
        names = {field.name for field in fields(ProposalRecord)}
        return {
            raw["request_id"]: ProposalRecord(**{k: v for k, v in raw.items() if k in names})
            for raw in payload.get("proposals", [])
        }

    def _write(self, records: dict[str, ProposalRecord]) -> None:
        payload = {"version": 1, "proposals": [asdict(record) for record in records.values()]}
        atomic_write(self._state, json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))

    def all(self) -> tuple[ProposalRecord, ...]:
        with self._locked():
            return tuple(self._read().values())

    def get(self, request_id: str) -> ProposalRecord | None:
        with self._locked():
            return self._read().get(request_id)

    def live_for_source(self, kind: str, source_id: str) -> ProposalRecord | None:
        key = approval_key(kind, source_id)
        live = [r for r in self.all() if r.key == key and r.status in LIVE_STATUSES | {"applied"}]
        return max(live, key=lambda r: r.revision) if live else None

    def create(self, record: ProposalRecord, proposal: Proposal) -> None:
        if proposal_hash(proposal) != record.action_hash:
            raise ProjectBoardError("proposal record hash does not match its proposal")
        with self._locked():
            records = self._read()
            atomic_write(self.frozen_path(record.request_id), canonical_json(proposal))
            records[record.request_id] = record
            self._write(records)

    def update(self, record: ProposalRecord) -> None:
        with self._locked():
            records = self._read()
            current = records.get(record.request_id)
            if current is None or current.action_hash != record.action_hash:
                raise ProjectBoardError("proposal record is absent or stale")
            if current.message_id not in {None, record.message_id}:
                raise ProjectBoardError("proposal message binding is immutable")
            records[record.request_id] = record
            self._write(records)

    def set_message(self, request_id: str, message_id: str, channel_id: str, version: str | None) -> None:
        with self._locked():
            records = self._read()
            current = records.get(request_id)
            if current is None:
                raise ProjectBoardError("proposal record is absent")
            if current.message_id is not None:
                if (current.message_id, current.channel_id) == (message_id, channel_id):
                    return
                raise ProjectBoardError("proposal is already bound to another card")
            records[request_id] = replace(
                current, message_id=message_id, channel_id=channel_id, status="posted",
                render_version=version or current.render_version,
            )
            self._write(records)

    def clear_message(self, key: str, action_hash: str, message_id: str) -> None:
        with self._locked():
            records = self._read()
            for request_id, record in records.items():
                if (record.key, record.action_hash, record.message_id) == (key, action_hash, message_id):
                    records[request_id] = replace(record, message_id=None, status="superseded")
            self._write(records)

    def frozen(self, record: ProposalRecord) -> Proposal:
        """The approved bytes; refused when they no longer hash to the record."""
        try:
            raw = json.loads(self.frozen_path(record.request_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ProjectBoardError("frozen proposal is unreadable") from error
        proposal = proposal_from_dict(raw)
        if proposal_hash(proposal) != record.action_hash:
            raise ProjectBoardError("frozen proposal changed after the request")
        return proposal


def atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, prefix=".tmp-", delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    temporary.replace(target)

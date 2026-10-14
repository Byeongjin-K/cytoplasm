from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, TypeGuard

from .choices import Choice

MemoryKind = Literal["memory", "user"]
RelocationStatus = Literal[
    "proposed",
    "posted",
    "approved",
    "written",
    "ingested",
    "approved",
    "written",
    "ingested",
    "reconciled",
    "abandoned",
]


class RelocationError(ValueError):
    """Persisted relocation state could not be parsed without guessing."""


@dataclass(frozen=True, slots=True)
class RelocationRecord:
    version: int
    source_kind: MemoryKind
    entry_sha256: str
    note_relpath: str
    note_plan_sha256: str
    reclaimable_chars: int
    action_hash: str
    status: RelocationStatus
    kind: str
    surface: str
    channel_id: str
    policy_version: int
    message_id: str | None
    created_at: str
    approved_at: str | None
    written_at: str | None
    reconciled_at: str | None
    remote_ref: str | None
    note_content_sha256: str | None
    rag_source_key: str | None
    rag_fingerprint: str | None
    backup_path: str | None
    last_block_reason: str | None
    #: Thread this request's approval message lives in. Optional so a record written
    #: before the per-request thread keeps parsing, and outside ``action_hash``.
    approval_thread_id: str | None = None
    approval_guild_id: str | None = None
    render_version: str | None = None
    #: Where the entry goes: None = Obsidian ops reference (v1), "person" = Obsidian person note,
    #: "memory" = MEMORY.md (a USER.md handling rule). Part of ``action_hash`` when set.
    destination: str | None = None
    #: Shared by every record one approval card covers (owner Q138: as few cards as possible).
    batch_hash: str | None = None
    #: Position on its versioned batch card.
    batch_index: int | None = None
    #: Peer-observed, durable execution receipt; not part of the proposed option-set hash.
    choice: Choice | None = None


DESTINATIONS: Final = frozenset({"person", "memory"})


@dataclass(frozen=True, slots=True)
class RelocationState:
    version: int
    relocations: Mapping[str, RelocationRecord]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relocations",
            MappingProxyType(dict(self.relocations)),
        )


_VERSION: Final = 1
_STATE_KEYS: Final = frozenset({"version", "relocations"})
_RECORD_KEYS: Final = frozenset(
    {
        "version",
        "source_kind",
        "entry_sha256",
        "note_relpath",
        "note_plan_sha256",
        "reclaimable_chars",
        "action_hash",
        "status",
        "kind",
        "surface",
        "channel_id",
        "policy_version",
        "message_id",
        "created_at",
        "approved_at",
        "written_at",
        "reconciled_at",
        "remote_ref",
        "note_content_sha256",
        "rag_source_key",
        "rag_fingerprint",
        "backup_path",
        "last_block_reason",
    }
)
#: Keys a record MAY carry — a row written before them still parses, and an
#: unknown key outside this set is still an unreadable shape (fail-closed).
_OPTIONAL_RECORD_KEYS: Final = frozenset(
    {"approval_thread_id", "approval_guild_id", "render_version", "destination", "batch_hash", "batch_index", "choice"}
)


def _is_mapping(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _mapping(value: object, field: str) -> dict[object, object]:
    if not _is_mapping(value):
        raise RelocationError(f"relocation state field {field!r} must be an object")
    return value


def _string(value: object, field: str) -> str:
    match value:
        case str() as parsed:
            return parsed
        case _:
            raise RelocationError(f"relocation state field {field!r} must be a string")


def _string_or_none(value: object, field: str) -> str | None:
    match value:
        case None:
            return None
        case str() as parsed:
            return parsed
        case _:
            raise RelocationError(
                f"relocation state field {field!r} must be a string or null"
            )


def _integer(value: object, field: str) -> int:
    match value:
        case int() as parsed if not isinstance(parsed, bool):
            return parsed
        case _:
            raise RelocationError(f"relocation state field {field!r} must be an integer")


def _require_version(value: object, field: str) -> int:
    parsed = _integer(value, field)
    if parsed != _VERSION:
        raise RelocationError(f"unsupported relocation {field}: {value!r}")
    return parsed


def _memory_kind(value: object) -> MemoryKind:
    match value:
        case "memory":
            return "memory"
        case "user":
            return "user"
        case _:
            raise RelocationError(f"unknown relocation source_kind: {value!r}")


def _relocation_status(value: object) -> RelocationStatus:
    match value:
        case "proposed":
            return "proposed"
        case "posted":
            return "posted"
        case "approved":
            return "approved"
        case "written":
            return "written"
        case "ingested":
            return "ingested"
        case "reconciled":
            return "reconciled"
        case "abandoned":
            return "abandoned"
        case _:
            raise RelocationError(f"unknown relocation status: {value!r}")


def _parse_record(raw: object) -> RelocationRecord:
    payload = _mapping(raw, "relocation")
    keys = frozenset(payload)
    if not _RECORD_KEYS <= keys or not keys <= _RECORD_KEYS | _OPTIONAL_RECORD_KEYS:
        raise RelocationError("relocation record has an unknown shape")
    return RelocationRecord(
        version=_require_version(payload["version"], "record version"),
        source_kind=_memory_kind(payload["source_kind"]),
        entry_sha256=_string(payload["entry_sha256"], "entry_sha256"),
        note_relpath=_string(payload["note_relpath"], "note_relpath"),
        note_plan_sha256=_string(payload["note_plan_sha256"], "note_plan_sha256"),
        reclaimable_chars=_integer(payload["reclaimable_chars"], "reclaimable_chars"),
        action_hash=_string(payload["action_hash"], "action_hash"),
        status=_relocation_status(payload["status"]),
        kind=_string(payload["kind"], "kind"),
        surface=_string(payload["surface"], "surface"),
        channel_id=_string(payload["channel_id"], "channel_id"),
        policy_version=_integer(payload["policy_version"], "policy_version"),
        message_id=_string_or_none(payload["message_id"], "message_id"),
        created_at=_string(payload["created_at"], "created_at"),
        approved_at=_string_or_none(payload["approved_at"], "approved_at"),
        written_at=_string_or_none(payload["written_at"], "written_at"),
        reconciled_at=_string_or_none(payload["reconciled_at"], "reconciled_at"),
        remote_ref=_string_or_none(payload["remote_ref"], "remote_ref"),
        note_content_sha256=_string_or_none(
            payload["note_content_sha256"], "note_content_sha256"
        ),
        rag_source_key=_string_or_none(payload["rag_source_key"], "rag_source_key"),
        rag_fingerprint=_string_or_none(payload["rag_fingerprint"], "rag_fingerprint"),
        backup_path=_string_or_none(payload["backup_path"], "backup_path"),
        last_block_reason=_string_or_none(
            payload["last_block_reason"], "last_block_reason"
        ),
        approval_thread_id=_string_or_none(
            payload.get("approval_thread_id"), "approval_thread_id"
        ),
        approval_guild_id=_string_or_none(payload.get("approval_guild_id"), "approval_guild_id"),
        render_version=_string_or_none(payload.get("render_version"), "render_version"),
        destination=_destination(payload.get("destination")),
        batch_hash=_string_or_none(payload.get("batch_hash"), "batch_hash"),
        batch_index=None if payload.get("batch_index") is None else _integer(payload.get("batch_index"), "batch_index"),
        choice=None if payload.get("choice") is None else Choice(_string(payload["choice"], "choice")),
    )


def _destination(value: object) -> str | None:
    if value is None or value in DESTINATIONS:
        return None if value is None else str(value)
    raise RelocationError(f"unknown relocation destination: {value!r}")


def empty_state() -> RelocationState:
    return RelocationState(version=_VERSION, relocations={})


def record_key(source_kind: MemoryKind, entry_sha256: str) -> str:
    return f"{source_kind}:{entry_sha256}"


def parse_state(raw: object) -> RelocationState:
    payload = _mapping(raw, "root")
    if frozenset(payload) != _STATE_KEYS:
        raise RelocationError("relocation state has an unknown top-level shape")
    version = _require_version(payload["version"], "state version")
    relocations_raw = _mapping(payload["relocations"], "relocations")
    relocations: dict[str, RelocationRecord] = {}
    for raw_key, raw_record in relocations_raw.items():
        key = _string(raw_key, "relocations key")
        record = _parse_record(raw_record)
        if key != record_key(record.source_kind, record.entry_sha256):
            raise RelocationError("relocation key does not match its record")
        relocations[key] = record
    return RelocationState(version=version, relocations=relocations)


def serialize_state(s: RelocationState) -> dict[str, object]:
    from .serialization import serialize_state as serialize

    return serialize(s)

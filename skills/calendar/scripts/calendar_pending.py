"""Private JSONL persistence for pending standalone calendar confirmations."""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TextIO

PENDING_CONFIRMS_ENV = "CALENDAR_PENDING_CONFIRMS"
DEFAULT_PENDING_CONFIRMS = Path("~/.hermes/calendar-gate/pending-confirms.jsonl")


class PendingConfirmError(RuntimeError):
    """The private pending-confirm store cannot be trusted."""


@dataclass(frozen=True, slots=True)
class PendingConfirm:
    """The immutable binding between a calendar draft and its owner approval message."""

    draft_id: str
    sha256: str
    dm_channel_id: str
    dm_message_id: str
    created: datetime
    render_version: str | None = field(default=None, compare=False, kw_only=True)
    key: str = field(default="", compare=False)
    kind: str | None = field(default=None, compare=False)
    surface: str | None = field(default=None, compare=False)
    channel_id: str = field(default="", compare=False)
    policy_version: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.key:
            object.__setattr__(self, "key", f"calendar:__orphan__:{self.draft_id}")
        if not self.channel_id:
            object.__setattr__(self, "channel_id", self.dm_channel_id)
        elif self.channel_id != self.dm_channel_id:
            raise PendingConfirmError("pending confirmation channel binding is inconsistent")

    def as_json(self) -> str:
        """Render the stable pending-confirm JSONL record in UTC."""
        record: dict[str, str | int] = {
            "created": self.created.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dm_channel_id": self.dm_channel_id,
            "dm_message_id": self.dm_message_id,
            "draft_id": self.draft_id,
            "key": self.key,
            "sha256": self.sha256,
        }
        if self.render_version is not None:
            record["render_version"] = self.render_version
        if self.kind is not None and self.surface is not None and self.policy_version is not None:
            record.update(
                {
                    "channel_id": self.channel_id,
                    "kind": self.kind,
                    "policy_version": self.policy_version,
                    "surface": self.surface,
                }
            )
        return json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class PendingConfirmStore:
    """Mode-600 JSONL store with flock-protected append and replacement."""

    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get(PENDING_CONFIRMS_ENV, "")
        self.path = path or Path(configured or DEFAULT_PENDING_CONFIRMS).expanduser()
        self._lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    def append(self, entry: PendingConfirm) -> None:
        """Append one pending confirmation without racing a watcher cleanup."""
        with self._lock():
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(entry.as_json() + "\n")
            self.path.chmod(0o600)

    def load(self) -> tuple[PendingConfirm, ...]:
        """Parse every entry before an approval decision can be made."""
        with self._lock():
            return self._read_unlocked()

    def remove_completed(
        self,
        snapshot: tuple[PendingConfirm, ...],
        retained: tuple[PendingConfirm, ...],
    ) -> None:
        """Remove processed snapshot entries without losing a concurrent append."""
        completed = {_entry_key(entry) for entry in snapshot} - {_entry_key(entry) for entry in retained}
        with self._lock():
            current = self._read_unlocked()
            self._write_unlocked(tuple(entry for entry in current if _entry_key(entry) not in completed))

    def drop(self, expected: PendingConfirm) -> None:
        """Compare-and-swap removal bound to the draft, message, and content hash."""
        binding = expected.draft_id, expected.dm_message_id, expected.sha256
        with self._lock():
            current = self._read_unlocked()
            self._write_unlocked(
                tuple(
                    entry
                    for entry in current
                    if (entry.draft_id, entry.dm_message_id, entry.sha256) != binding
                )
            )

    def _read_unlocked(self) -> tuple[PendingConfirm, ...]:
        if not self.path.exists():
            return ()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            return tuple(_parse_entry(json.loads(line)) for line in lines if line)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise PendingConfirmError("pending store schema is invalid") from error

    def _write_unlocked(self, entries: tuple[PendingConfirm, ...]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            temporary.write_text("".join(f"{entry.as_json()}\n" for entry in entries), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.path)
            self.path.chmod(0o600)
        except OSError as error:
            raise PendingConfirmError("pending store write failed") from error

    def _lock(self) -> _StoreLock:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = self._lock_path.open("a", encoding="utf-8")
        self._lock_path.chmod(0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return _StoreLock(handle)


@dataclass(frozen=True, slots=True)
class _StoreLock:
    handle: TextIO

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def _parse_entry(raw: dict[str, str | int]) -> PendingConfirm:
    created = datetime.fromisoformat(raw["created"].replace("Z", "+00:00"))
    values = ("draft_id", "sha256", "dm_channel_id", "dm_message_id")
    if created.tzinfo is None or not all(raw[name] for name in values):
        raise ValueError("pending confirm entry is invalid")
    draft_id = raw["draft_id"]
    kind, surface = raw.get("kind"), raw.get("surface")
    channel_id = raw.get("channel_id", raw["dm_channel_id"])
    policy_version = raw.get("policy_version")
    render_version = raw.get("render_version")
    if (
        not isinstance(draft_id, str)
        or not isinstance(kind, str | None)
        or not isinstance(surface, str | None)
        or not isinstance(channel_id, str)
        or not isinstance(policy_version, int | None)
        or render_version not in (None, "1", "2", "3", "4", "5")
    ):
        raise ValueError("pending confirm binding is invalid")
    key = raw.get("key") or _legacy_key(draft_id)
    return PendingConfirm(
        draft_id=draft_id, sha256=raw["sha256"], dm_channel_id=raw["dm_channel_id"],
        dm_message_id=raw["dm_message_id"], created=created.astimezone(UTC), key=key,
        kind=kind, surface=surface, channel_id=channel_id, policy_version=policy_version,
        render_version=render_version,
    )


def _entry_key(entry: PendingConfirm) -> tuple[str, str]:
    return entry.draft_id, entry.dm_message_id


def _legacy_key(draft_id: str) -> str:
    gate_dir = Path(os.environ.get("CALENDAR_GATE_DIR", "~/.hermes/calendar-gate")).expanduser()
    try:
        draft = json.loads((gate_dir / "drafts" / f"{draft_id}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"calendar:__orphan__:{draft_id}"
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("calendar draft for pending confirm is unreadable") from error
    if not isinstance(draft, dict):
        raise ValueError("calendar draft for pending confirm is invalid")
    calendar_id = draft.get("calendar_id")
    event_id = draft.get("event_id")
    start = draft.get("start")
    if not isinstance(calendar_id, str) or not calendar_id:
        raise ValueError("calendar draft calendar_id is invalid")
    subject = event_id if isinstance(event_id, str) and event_id else start
    if not isinstance(subject, str) or not subject:
        raise ValueError("calendar draft event_id/start is invalid")
    return f"calendar:{calendar_id}:{subject}"

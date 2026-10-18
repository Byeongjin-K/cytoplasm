"""One owner-approved Obsidian note request: exact path, frozen body, bound card.

Before this module there was no way for the agent to ask for an arbitrary note write.
plaud_sync and memory_relocate each own a producer, but a plain "save this note to
Obsidian at <path>" request had no CLI, so the agent improvised a draft and asked the
owner to ✅ its own chat message — a reaction nothing consumes (2026-10-07). A request
here freezes the body bytes, binds the card to the external-effect gate's own action
hash for exactly (relpath, title, body), and is the only record the watcher writes from.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path, PurePosixPath
from typing import Final

from .config import ObsidianWriteError
from .note import NotePlan

STATE_ROOT_ENV: Final = "OBSIDIAN_NOTE_REQUEST_ROOT"
DEFAULT_STATE_ROOT: Final = Path.home() / ".hermes" / "obsidian-write-requests"
PARA_ROOTS: Final = frozenset({"000_PARA", "001_KIMM_PARA"})
_FORBIDDEN: Final = frozenset('*"\\<>:|?#^[]')
LIVE_STATUSES: Final = frozenset({"proposed", "posted", "approved"})


@dataclass(frozen=True, slots=True)
class NoteRequest:
    request_id: str
    key: str
    relpath: str
    title: str
    body_sha256: str
    action_hash: str
    target_id: str
    created_at: str
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
    overwrite: bool = False
    unresolved_links: tuple[str, ...] = ()
    links_checked: bool = False
    related: tuple[str, ...] = ()
    related_source: str = ""
    attempts: int = 0
    last_error: str = ""
    remote_ref: str = ""
    content_sha256: str = ""
    finished_at: str = ""
    method: str = ""


def state_root() -> Path:
    override = os.environ.get(STATE_ROOT_ENV)
    return Path(override).expanduser() if override else DEFAULT_STATE_ROOT


def validate_relpath(raw: str) -> PurePosixPath:
    """The exact vault path the owner named — normalized, never re-derived or shortened."""
    text = unicodedata.normalize("NFC", raw.strip())
    relpath = PurePosixPath(text)
    parts = relpath.parts
    if not text or relpath.is_absolute() or len(parts) < 2:
        raise ObsidianWriteError("note path must be a vault-relative path under a PARA root", False)
    if parts[0] not in PARA_ROOTS:
        raise ObsidianWriteError(f"note path must start with one of {sorted(PARA_ROOTS)}", False)
    if relpath.suffix != ".md":
        raise ObsidianWriteError("note path must end with .md", False)
    for part in parts:
        if part in {".", ".."} or part.startswith(".") or part != part.strip():
            raise ObsidianWriteError("note path has an unsafe segment", False)
        if any(char in _FORBIDDEN or ord(char) < 32 for char in part):
            raise ObsidianWriteError("note path has a character Obsidian cannot store", False)
    return relpath


def build_plan(relpath: PurePosixPath, body: str, title: str | None) -> NotePlan:
    """Title defaults to a leading ``# H1`` (removed from the body) or the file stem.

    ``render_note`` writes ``# <title>`` itself, so a body that keeps its own H1 would
    show the heading twice.
    """
    text = unicodedata.normalize("NFC", body).strip()
    first, _, rest = text.partition("\n")
    heading = first[2:].strip() if first.startswith("# ") else None
    chosen = " ".join((title or heading or relpath.stem).split())
    if heading is not None and heading == chosen:
        text = rest.strip()
    if not chosen or not text:
        raise ObsidianWriteError("note title and body must not be empty", False)
    return NotePlan(relpath, chosen, text)


def body_sha256(plan: NotePlan) -> str:
    return hashlib.sha256(plan.body.encode("utf-8")).hexdigest()


def request_key(relpath: str) -> str:
    return "obsidian-note:" + hashlib.sha256(relpath.encode("utf-8")).hexdigest()[:24]


class NoteRequestStore:
    """JSON records + frozen bodies under one flock; every write is atomic 0600."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or state_root()

    @property
    def _state(self) -> Path:
        return self.root / "requests.json"

    def body_path(self, request_id: str) -> Path:
        return self.root / "bodies" / f"{request_id}.md"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.root / "store.lock").open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, NoteRequest]:
        try:
            payload = json.loads(self._state.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as error:
            raise ObsidianWriteError("note request store is unreadable", False) from error
        names = {field.name for field in fields(NoteRequest)}
        records: dict[str, NoteRequest] = {}
        for raw in payload.get("requests", []):
            values = {name: value for name, value in raw.items() if name in names}
            for name in ("unresolved_links", "related"):
                values[name] = tuple(values.get(name, ()))
            records[raw["request_id"]] = NoteRequest(**values)
        return records

    def _write(self, records: dict[str, NoteRequest]) -> None:
        payload = {"version": 1, "requests": [asdict(record) for record in records.values()]}
        _atomic_write(self._state, json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))

    def all(self) -> tuple[NoteRequest, ...]:
        with self._locked():
            return tuple(self._read().values())

    def get(self, request_id: str) -> NoteRequest | None:
        with self._locked():
            return self._read().get(request_id)

    def create(self, record: NoteRequest, plan: NotePlan) -> None:
        with self._locked():
            records = self._read()
            _atomic_write(self.body_path(record.request_id), plan.body)
            records[record.request_id] = record
            self._write(records)

    def update(self, record: NoteRequest) -> None:
        with self._locked():
            records = self._read()
            current = records.get(record.request_id)
            if current is None or current.action_hash != record.action_hash:
                raise ObsidianWriteError("note request record is absent or stale", False)
            if current.message_id not in {None, record.message_id}:
                raise ObsidianWriteError("note request message binding is immutable", False)
            records[record.request_id] = record
            self._write(records)

    def set_message(self, request_id: str, message_id: str, channel_id: str, version: str | None) -> None:
        with self._locked():
            records = self._read()
            current = records.get(request_id)
            if current is None:
                raise ObsidianWriteError("note request record is absent", False)
            if current.message_id is not None:
                if (current.message_id, current.channel_id) == (message_id, channel_id):
                    return
                raise ObsidianWriteError("note request is already bound to another card", False)
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

    def frozen_plan(self, record: NoteRequest) -> NotePlan:
        """The approved bytes, refused when the stored body no longer hashes to the record."""
        try:
            body = self.body_path(record.request_id).read_text(encoding="utf-8")
        except OSError as error:
            raise ObsidianWriteError("frozen note body is unreadable", False) from error
        plan = NotePlan(PurePosixPath(record.relpath), record.title, body)
        if body_sha256(plan) != record.body_sha256:
            raise ObsidianWriteError("frozen note body changed after the request", False)
        return plan


def _atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, prefix=".tmp-", delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    temporary.replace(target)

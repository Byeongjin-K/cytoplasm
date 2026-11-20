"""Atomic persistence for plaud-sync state and frozen note bodies."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Protocol, TypeAlias

from .model import (
    PlaudSyncError,
    PlaudSyncRecord,
    PlaudSyncState,
    empty_state,
    parse_state,
    serialize_state,
)

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)

_PENDING_STATUSES: Final = frozenset({"planned", "posted", "approved"})
_SAFE_RECORDING_ID: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_NOTES_DIRNAME: Final = "notes"
_TRANSCRIPTS_DIRNAME: Final = "transcripts"


class JsonLoader(Protocol):
    def __call__(self, s: str) -> JsonValue: ...


_JSON_LOADS: JsonLoader = json.loads


class PlaudSyncStoreError(RuntimeError):
    """A store operation would violate an immutable binding or a safe path."""


def load_state(path: Path) -> PlaudSyncState:
    try:
        document = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_state()
    except UnicodeDecodeError as error:
        raise PlaudSyncError(f"plaud-sync state is not UTF-8: {path}") from error
    except OSError as error:
        raise PlaudSyncError(f"cannot read plaud-sync state: {path}") from error
    try:
        raw = _JSON_LOADS(document)
    except json.JSONDecodeError as error:
        raise PlaudSyncError(f"plaud-sync state is not valid JSON: {path}") from error
    return parse_state(raw)


def _atomic_write(path: Path, payload: str) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.name}.",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                _ = temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    temporary_path = None
    except OSError as error:
        raise PlaudSyncError(f"cannot save plaud-sync file: {path}") from error


def save_state(path: Path, state: PlaudSyncState) -> None:
    serialized = (
        json.dumps(
            serialize_state(state),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    _atomic_write(path, serialized)


def _note_path(state_dir: Path, recording_id: str) -> Path:
    if not _SAFE_RECORDING_ID.match(recording_id) or ".." in recording_id:
        raise PlaudSyncStoreError(f"plaud recording id is not filesystem-safe: {recording_id!r}")
    return state_dir / _NOTES_DIRNAME / f"{recording_id}.md"


def save_note_body(state_dir: Path, recording_id: str, body: str) -> None:
    _atomic_write(_note_path(state_dir, recording_id), body)


def transcript_path(state_dir: Path, stem: str) -> Path:
    return state_dir / _TRANSCRIPTS_DIRNAME / f"{stem}.md"


def save_transcript(state_dir: Path, stem: str, markdown: str) -> Path:
    path = transcript_path(state_dir, stem)
    _atomic_write(path, markdown)
    return path


def _kept_path(state_dir: Path, stem: str) -> Path:
    return state_dir / _TRANSCRIPTS_DIRNAME / ".kept" / f"{stem}.json"


def keep_transcript(state_dir: Path, stem: str, path: Path, model: str) -> None:
    """Point the next attempt at a transcript the CLI already finished (the transcript stays put)."""
    _atomic_write(_kept_path(state_dir, stem), json.dumps({"transcript_path": str(path), "model": model}))


def kept_transcript(state_dir: Path, stem: str) -> tuple[Path, str] | None:
    try:
        raw: JsonValue = json.loads(_kept_path(state_dir, stem).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path = raw.get("transcript_path") if isinstance(raw, dict) else None
    if not isinstance(path, str) or not Path(path).is_file():
        return None
    model = raw.get("model") if isinstance(raw, dict) else None
    return Path(path), model if isinstance(model, str) else ""


def release_kept_transcript(state_dir: Path, stem: str) -> None:
    """Drop only the pointer once the record left transcribing; the transcript file is never removed."""
    _kept_path(state_dir, stem).unlink(missing_ok=True)


def load_note_body(state_dir: Path, recording_id: str) -> str | None:
    try:
        return _note_path(state_dir, recording_id).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PlaudSyncStoreError(f"cannot read plaud note body: {recording_id}") from error


@dataclass(frozen=True, slots=True)
class PlaudSyncStore:
    state_path: Path

    def all_pending(self) -> tuple[PlaudSyncRecord, ...]:
        return tuple(
            record
            for record in load_state(self.state_path).records.values()
            if record.status in _PENDING_STATUSES
        )

    def pending(self) -> tuple[PlaudSyncRecord, ...]:
        return self.all_pending()

    def set_message_id(
        self, record: PlaudSyncRecord, message_id: str, channel_id: str
    ) -> None:
        state, current = self._current(record.recording_id)
        if current.action_hash != record.action_hash:
            raise PlaudSyncStoreError("plaud approval message id is already bound or stale")
        if current.message_id is not None:
            if (current.message_id, current.channel_id) == (message_id, channel_id):
                return
            raise PlaudSyncStoreError("plaud approval message id is already bound or stale")
        self._persist(state, replace(current, message_id=message_id, channel_id=channel_id, render_version=record.render_version))

    def clear_message_id(self, key: str, action_hash: str, message_id: str) -> None:
        state, current = self._current(key)
        if (current.action_hash, current.message_id) == (action_hash, message_id):
            self._persist(state, replace(current, message_id=None))

    def update(self, record: PlaudSyncRecord) -> None:
        state, current = self._current(record.recording_id)
        if (
            current.action_hash != record.action_hash
            or current.message_id != record.message_id
        ):
            raise PlaudSyncStoreError("plaud update would change an immutable message binding")
        self._persist(state, record)

    def _current(self, key: str) -> tuple[PlaudSyncState, PlaudSyncRecord]:
        state = load_state(self.state_path)
        record = state.records.get(key)
        if record is None:
            raise PlaudSyncStoreError(f"plaud-sync record is absent: {key}")
        return state, record

    def _persist(self, state: PlaudSyncState, record: PlaudSyncRecord) -> None:
        records = dict(state.records)
        records[record.recording_id] = record
        save_state(
            self.state_path,
            PlaudSyncState(state.version, state.last_poll_at, records),
        )

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from .model import RelocationRecord, RelocationState, record_key
from .store import load_state, save_state

_PENDING_STATUSES: Final = frozenset({"proposed", "posted", "approved", "written", "ingested"})
RelocationStoreError = RuntimeError


@dataclass(frozen=True, slots=True)
class RelocationStore:
    state_path: Path

    def all_pending(self) -> tuple[RelocationRecord, ...]:
        return tuple(record for record in load_state(self.state_path).relocations.values() if record.status in _PENDING_STATUSES)

    def pending(self) -> tuple[RelocationRecord, ...]:
        return self.all_pending()

    def set_message_id(self, record: RelocationRecord, message_id: str, channel_id: str) -> None:
        """Bind the posted message AND the surface it was posted to, atomically.

        The channel is part of the binding (interop invariant: the surface is persisted on the
        record). Losing it makes the next tick probe an empty channel, read MISSING, and discard
        the owner's ✅ — so both fields are written in one no-overwrite commit.
        """
        state, current = self._current(record_key(record.source_kind, record.entry_sha256))
        if current.action_hash != record.action_hash:
            raise RelocationStoreError("relocation approval message id is already bound or stale")
        if current.message_id is not None:
            if (current.message_id, current.channel_id) == (message_id, channel_id):
                return
            raise RelocationStoreError("relocation approval message id is already bound or stale")
        self._persist(state, replace(current, message_id=message_id, channel_id=channel_id, render_version=record.render_version))

    def clear_message_id(self, key: str, action_hash: str, message_id: str) -> None:
        state, current = self._current(key)
        if (current.action_hash, current.message_id) == (action_hash, message_id):
            self._persist(state, replace(current, message_id=None))

    def clear_batch_message_id(self, batch_hash: str, message_id: str) -> None:
        """Unbind every member of one batch card, only where it still holds that exact message."""
        state = load_state(self.state_path)
        records = dict(state.relocations)
        changed = False
        for key, record in state.relocations.items():
            if record.batch_hash == batch_hash and record.message_id == message_id:
                records[key] = replace(record, message_id=None)
                changed = True
        if changed:
            save_state(self.state_path, RelocationState(state.version, records))

    def update(self, record: RelocationRecord) -> None:
        state, current = self._current(record_key(record.source_kind, record.entry_sha256))
        if current.action_hash != record.action_hash or current.message_id != record.message_id:
            raise RelocationStoreError("relocation update would change an immutable message binding")
        self._persist(state, record)

    def _current(self, key: str) -> tuple[RelocationState, RelocationRecord]:
        state = load_state(self.state_path)
        record = state.relocations.get(key)
        if record is None:
            raise RelocationStoreError("relocation record is absent")
        return state, record

    def _persist(self, state: RelocationState, record: RelocationRecord) -> None:
        records = dict(state.relocations)
        records[record_key(record.source_kind, record.entry_sha256)] = record
        save_state(self.state_path, RelocationState(state.version, records))

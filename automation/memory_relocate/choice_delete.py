"""Exact native deletion with a per-store write-ahead receipt against replay."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.deletion import delete_entry
from automation.memory_curator.model import MemoryKind
from automation.memory_curator.watch_steps import read_native

from .choice_files import atomic_text
from .choice_request import ChoiceRequest
from .choices import ChoiceError
from .choice_json import decode

_KINDS: Final[tuple[MemoryKind, ...]] = ("user", "memory")


@dataclass(frozen=True, slots=True)
class NativeDelete:
    memory_dir: Path
    receipts: Path
    now: datetime

    def apply(self, request: ChoiceRequest, number: int) -> tuple[str, ...]:
        """The caller already checked Discord; no caller-supplied path reaches deletion."""
        record, text = request.members[number]
        if entry_digest(record.source_kind, text) != record.entry_sha256:
            raise ChoiceError("delete_digest")
        backups: list[str] = []
        for kind in _KINDS:
            receipt = self.receipts / f"{request.key}.{record.entry_sha256}.{kind}.json"
            try:
                document = receipt.read_text(encoding="utf-8")
            except FileNotFoundError:
                document = None
            if document is not None:
                match decode(document):
                    case {"status": "done", "backup": str() as backup}:
                        if backup:
                            backups.append(backup)
                        continue  # Never re-delete text added after this completed operation.
                    case _:
                        raise ChoiceError("delete-interrupted")
            raw, memory = read_native(self.memory_dir, kind)
            indices = [index for index, entry in enumerate(memory.entries) if entry.text == text]
            if len(indices) > 1:
                raise ChoiceError("delete_ambiguous")
            backup = ""
            if indices:
                atomic_text(receipt, '{"status":"started"}')
                outcome = delete_entry(self.memory_dir, kind, indices[0], expected_bytes=raw, now=self.now)
                backup = str(outcome.backup_path)
                backups.append(backup)
            atomic_text(receipt, json.dumps({"status": "done", "backup": backup}))
        return tuple(backups)

"""Received-time analysis cursor; storage timestamps cannot make historical mail new."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import ProjectBoardError
from .proposals import atomic_write


def received_time(value: str) -> datetime:
    """Normalize ISO mail dates; naive MailOn dates use the producer's local timezone."""
    return datetime.fromisoformat(value).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MailWatermark:
    received_at: datetime
    uids: frozenset[str] = frozenset()

    def advance(self, received_at: datetime, uid: str) -> MailWatermark:
        """Keep every processed UID at the boundary, including late same-time arrivals."""
        uids = self.uids if received_at == self.received_at else frozenset()
        return MailWatermark(received_at, uids | {uid})


def read_watermark(root: Path) -> MailWatermark | None:
    """Legacy storage-time state restarts at now, deliberately abandoning old backlog."""
    try:
        raw = json.loads((root / "analyze-state.json").read_text(encoding="utf-8"))
        if "mail_received_at" not in raw:
            int(raw["mail_saved_at"])
            return None
        stamp = received_time(raw["mail_received_at"])
        uids = raw["mail_received_uids"]
        if not isinstance(uids, list) or not all(isinstance(uid, str) for uid in uids):
            raise ProjectBoardError("analysis watermark UIDs must be a list of strings")
        return MailWatermark(stamp, frozenset(uids))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProjectBoardError("analysis watermark is unreadable") from error


def write_watermark(root: Path, watermark: MailWatermark) -> None:
    atomic_write(root / "analyze-state.json", json.dumps({
        "mail_received_at": watermark.received_at.isoformat(),
        "mail_received_uids": sorted(watermark.uids),
    }))

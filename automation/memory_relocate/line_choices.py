"""One exact entry, one lifecycle request, one immediate owner choice."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Final

from .choices import Choice, OPTIONS, owner_reacted
from .model import RelocationRecord, record_key

RENDER_VERSION: Final = "mc-reloc-line-render-v1"
REACTIONS: Final = ("➡️", "📌", "🗑️")


def read_choice(
    read_users: Callable[[str], tuple[tuple[str, bool], ...]], owner_id: str,
) -> Choice | None:
    """No reaction is pending; multiple owner choices conservatively keep."""
    selected = [
        choice for emoji, choice in zip(REACTIONS, OPTIONS, strict=True)
        if owner_reacted(read_users(emoji), owner_id)
    ]
    return selected[0] if len(selected) == 1 else Choice.KEEP if selected else None


def line_hash(record: RelocationRecord) -> str:
    """The exact source-qualified text digest and existing key bind the option set."""
    encoded = json.dumps({
        "version": RENDER_VERSION, "key": record_key(record.source_kind, record.entry_sha256),
        "text_digest": record.entry_sha256, "move": record.action_hash,
        "options": list(OPTIONS), "default": None, "conflict": Choice.KEEP,
        "delete": {"accounts": ["agent", "peer"], "stores": ["user", "memory"], "backup": True},
    }, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def build_lines(members: Sequence[tuple[RelocationRecord, str]]) -> tuple[RelocationRecord, ...]:
    """Bind every postable line independently, without the old five-row ceiling."""
    from .render import RenderError, prepare_approval_card

    records: list[RelocationRecord] = []
    for record, text in members:
        bound = replace(
            record, batch_hash=line_hash(record), batch_index=1,
            render_version=RENDER_VERSION, message_id=None, status="proposed", choice=None,
        )
        try:
            _ = prepare_approval_card(bound, text)
        except RenderError:
            continue
        records.append(bound)
    return tuple(records)

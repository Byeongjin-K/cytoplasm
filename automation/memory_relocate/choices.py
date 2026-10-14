"""Versioned, finite owner choices for one batch approval."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import Final

RENDER_VERSION: Final = "mc-reloc-batch-render-v2"
HASH_VERSION: Final = "mc-reloc-batch-v2"


class Choice(StrEnum):
    MOVE = "move"
    KEEP = "keep"
    DELETE = "delete"


# Five rows use 17 reactions, leaving three slots for unrelated owner reactions.
ROW_REACTIONS: Final = (
    ("🔴", "🟥", "❤️"), ("🟠", "🟧", "🧡"), ("🟡", "🟨", "💛"),
    ("🟢", "🟩", "💚"), ("🔵", "🟦", "💙"),
)
MAX_ROWS: Final = len(ROW_REACTIONS)
OPTIONS: Final = (Choice.MOVE, Choice.KEEP, Choice.DELETE)


def owner_reacted(users: tuple[tuple[str, bool], ...], owner_id: str) -> bool:
    return any(user_id == owner_id and not is_bot for user_id, is_bot in users)


def read_choices(
    read_users: Callable[[str], tuple[tuple[str, bool], ...]], owner_id: str, count: int,
) -> tuple[Choice, ...]:
    """Take one complete snapshot; an ambiguous row conservatively keeps its entry."""
    if not 1 <= count <= MAX_ROWS:
        raise ChoiceError("row_count")
    choices: list[Choice] = []
    for emojis in ROW_REACTIONS[:count]:
        selected = [
            choice for emoji, choice in zip(emojis, OPTIONS, strict=True)
            if owner_reacted(read_users(emoji), owner_id)
        ]
        choices.append(selected[0] if len(selected) == 1 else Choice.KEEP if selected else Choice.MOVE)
    return tuple(choices)


def batch_choices_hash(member_action_hashes: Sequence[str]) -> str:
    """Bind order, the authorized option set, conflict/default and both-store delete scope."""
    encoded = json.dumps({
        "version": HASH_VERSION, "members": list(member_action_hashes),
        "options": list(OPTIONS), "default": Choice.MOVE, "conflict": Choice.KEEP,
        "delete": {"accounts": ["agent", "peer"], "stores": ["user", "memory"], "backup": True},
    }, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class ChoiceError(ValueError):
    """A choice request could not establish its bounded authorization contract."""

"""Attach a recall that finished after its turn started, at the next model call of that turn.

Owner Q155 (2026-10-15): the pick takes a few seconds, so the turn does not wait for it; the
gateway's ``llm_request`` middleware inserts the result once it is ready. The note goes in at a
fixed position (where the request ended when it was first attached) so later calls in the same
turn keep the same prompt prefix.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Final

MATCH_CHARS: Final = 200
FENCE_OPEN: Final = "<memory-context>"


@dataclass(slots=True)
class Recall:
    query: str
    done: threading.Event = field(default_factory=threading.Event)
    block: str = ""
    delivered: bool = False
    index: int | None = None


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _latest_owner_text(items: list[Any]) -> str | None:
    for item in reversed(items):
        if isinstance(item, dict) and item.get("role") == "user":
            text = _text(item.get("content"))
            if not text.lstrip().startswith(FENCE_OPEN):
                return text
    return None


def _note(items: list[Any], fenced: str) -> dict[str, Any]:
    for item in reversed(items):
        content = item.get("content") if isinstance(item, dict) and item.get("role") == "user" else None
        if isinstance(content, list) and content and isinstance(content[0], dict):
            return {"role": "user", "content": [{"type": content[0].get("type", "input_text"), "text": fenced}]}
    return {"role": "user", "content": fenced}


def attach(request: dict[str, Any], recall: Recall, fenced: str) -> dict[str, Any] | None:
    """The request with the note inserted, or None when nothing should change."""
    if recall.delivered or not recall.done.is_set() or not recall.block or not fenced:
        return None
    key = next((name for name in ("input", "messages") if isinstance(request.get(name), list)), None)
    if key is None:
        return None
    items: list[Any] = list(request[key])
    latest = _latest_owner_text(items)
    needle = recall.query.strip()[:MATCH_CHARS]
    if latest is None or not needle or needle not in latest:
        return None
    index = len(items) if recall.index is None else recall.index
    if index > len(items):
        return None
    recall.index = index
    return {**request, key: [*items[:index], _note(items, fenced), *items[index:]]}

"""Hermes MemoryProvider discovered at $HERMES_HOME/plugins/autophagy_recall/.

Owner Q155 (2026-10-15): the whole store is searched (one passage per note, top 100) and one
call to the account's main model picks what the turn needs. The pick runs in the background;
the turn waits for it at most the owner's 5 s (2026-10-14) and otherwise starts at once, and the
``llm_request`` middleware attaches the result at the next model call of the same turn. Native
memory, the system prompt, and sync hooks are untouched.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Final, Never

from agent.memory_provider import MemoryProvider

from automation.memory_provider.attach import Recall, attach, claim_turn

_WAIT_SECONDS: Final = 5.0  # owner 2026-10-14: "2초보다 더 길게 해야하는거 아니야?"
_PICK_TIMEOUT_SECONDS: Final = 30.0
_CHAR_LIMIT: Final = 4000
_QUERY_LIMIT: Final = 2000
_LOGGER: Final = logging.getLogger(__name__)
_RECALLS: dict[str, Recall] = {}
_RECALLS_LOCK: Final = threading.Lock()
_MIDDLEWARE_REGISTERED: list[bool] = []

Lookup = Callable[[str], str]


def _complete(prompt: str) -> str:
    from agent.auxiliary_client import call_llm

    response = call_llm(messages=[{"role": "user", "content": prompt}], timeout=_PICK_TIMEOUT_SECONDS)
    return str(response.choices[0].message.content or "")


def pick_reference(query: str) -> str:
    """One whole-store pick, formatted as the reference block; raises on lookup failure."""
    from automation.knowledge.pack import KnowledgeQuery
    from automation.knowledge.pick import pick_evidence
    from automation.memory_provider.worker import HIT_LIMIT, format_reference

    pack = pick_evidence(KnowledgeQuery(
        query, sources=frozenset({"rag"}), limit=HIT_LIMIT, caller="memory-provider",
    ), _complete)
    if pack.verdict == "unavailable":
        raise LookupError("knowledge lookup unavailable")
    return format_reference(pack) if pack.verdict == "hit" else ""


def _resolve(recall: Recall, lookup: Lookup) -> None:
    try:
        block = lookup(recall.query)
    except LookupError:
        _LOGGER.warning("MEMORY-RECALL-SKIP reason=lookup")
        block = ""
    except Exception:  # noqa: BLE001 - recall is optional; the turn must never fail on it
        _LOGGER.warning("MEMORY-RECALL-SKIP reason=error")
        block = ""
    if len(block) > _CHAR_LIMIT:
        _LOGGER.warning("MEMORY-RECALL-SKIP reason=oversize")
        block = ""
    recall.block = block
    recall.done.set()


def request_middleware(*, request: dict[str, Any], **context: Any) -> dict[str, Any] | None:
    with _RECALLS_LOCK:
        recall = _RECALLS.get(str(context.get("session_id") or ""))
    if recall is None or not claim_turn(recall, str(context.get("turn_id") or "")) or not recall.block:
        return None
    from agent.memory_manager import build_memory_context_block

    changed = attach(request, recall, build_memory_context_block(recall.block))
    return None if changed is None else {"request": changed}


class AutophagyRecallProvider(MemoryProvider):
    def __init__(self, lookup: Lookup = pick_reference, wait_seconds: float = _WAIT_SECONDS) -> None:
        self._lookup = lookup
        self._wait_seconds = wait_seconds
        self._session_id = ""

    @property
    def name(self) -> str:
        return "autophagy_recall"

    def is_available(self) -> bool:
        """No network check during discovery."""
        return True

    def initialize(self, session_id: str, **kwargs: str) -> None:
        """No store or conversation writes; Hermes' prefetch passes no session id, so keep it here."""
        self._session_id = session_id

    def on_session_switch(self, new_session_id: str, **kwargs: object) -> None:
        with _RECALLS_LOCK:
            pending = _RECALLS.pop(self._session_id, None)
            if pending is not None:
                _RECALLS[new_session_id] = pending
        self._session_id = new_session_id

    def get_tool_schemas(self) -> list[Never]:
        return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not query.strip():
            return ""
        recall = Recall(query[:_QUERY_LIMIT])
        with _RECALLS_LOCK:
            _RECALLS[session_id or self._session_id] = recall
        threading.Thread(
            target=_resolve, args=(recall, self._lookup), daemon=True, name="autophagy-recall",
        ).start()
        if recall.done.wait(self._wait_seconds):
            recall.delivered = True
            return recall.block
        _LOGGER.info("MEMORY-RECALL-DEFER")
        return ""


def register(ctx: Any) -> None:
    ctx.register_memory_provider(AutophagyRecallProvider())
    if not _MIDDLEWARE_REGISTERED:
        ctx.register_middleware("llm_request", request_middleware)
        _MIDDLEWARE_REGISTERED.append(True)

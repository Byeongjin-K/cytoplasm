"""The agent's own recall call gets the same whole-store pick as automatic recall (owner Q150).

Any model or search-shape failure falls back to the plain search, reported as not picked, so a
deployment order where the RAG server is not yet rebuilt still answers.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

Rows = list[dict[str, Any]]
Picker = Callable[[Any, str, int, type[Exception]], tuple[Rows, bool]]


def picker(env: Mapping[str, str] = os.environ) -> Picker | None:
    if env.get("RECALL_PICK", "1") == "0":
        return None

    def pick(client: Any, query: str, limit: int, fatal: type[Exception]) -> tuple[Rows, bool]:
        from automation.codex_llm import CodexClient, CodexError
        from automation.knowledge.pick import attached_rows, choose, store_search

        try:
            chosen = choose(query, store_search(client), CodexClient.from_environment().complete)
        except (CodexError, ValueError, fatal):
            return [dict(row) for row in client.search_memory(query, limit=limit)], False
        return attached_rows(chosen), True

    return pick

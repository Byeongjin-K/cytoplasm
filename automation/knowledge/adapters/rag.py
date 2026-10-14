"""Read-only RAG adapter using the existing MCP memory client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from automation.knowledge.core import (
    analyze_entity_intent,
    grounded_rows,
    merge_entity_rows,
)
from automation.knowledge.pack import EvidenceItem, KnowledgeQuery
from automation.knowledge.plan import QueryPlan
from automation.knowledge.rank import item_from_rag
from automation.rag_ingest.config import load_config
from automation.rag_ingest.mcp_client import McpFatalError, McpMemoryClient, McpUnreachableError

_CONFIG_DEFAULT = "~/.hermes/rag-ingest/config.json"


@dataclass(frozen=True, slots=True)
class RagFetch:
    items: tuple[EvidenceItem, ...]
    status: str
    notes: tuple[str, ...]


def memory_client(env: Mapping[str, str]) -> McpMemoryClient:
    config = load_config(Path(env.get("KNOWLEDGE_RAG_CONFIG", env.get("RECALL_CONFIG", _CONFIG_DEFAULT))).expanduser())
    return McpMemoryClient(config.mcp_base_url, config.api_key)


def _enabled(env: Mapping[str, str], key: str) -> bool:
    return env.get(key, "").casefold() in {"1", "true", "yes", "on"}


def _hybrid_argument_is_unsupported(error: McpFatalError) -> bool:
    message = str(error).casefold()
    return "entity_anchors" in message and (
        "validation" in message or "unexpected" in message or "iserror" in message
    )


def fetch_rag(query: KnowledgeQuery, plan: QueryPlan, env: Mapping[str, str]) -> RagFetch:
    """Use MCP hybrid search, degrading to the old call shape on old deployments."""
    try:
        client = memory_client(env)
        search_text = f"{query.text} {plan.source_hint}" if plan.source_hint else query.text
        intent = analyze_entity_intent(query.text)
        if intent.matches:
            try:
                found = client.search_memory(
                    search_text,
                    limit=query.limit,
                    entity_anchors=intent.entity_hints,
                )
            except McpFatalError as error:
                if not _hybrid_argument_is_unsupported(error):
                    raise
                found = client.search_memory(search_text, limit=query.limit)
        else:
            found = client.search_memory(search_text, limit=query.limit)
        rows = [dict(row) for row in found]
        hits = grounded_rows(query.text, rows)
        if not hits and _enabled(env, "KNOWLEDGE_ENTITY_FALLBACK"):
            if intent.matches:
                fallback_query = " ".join(intent.entity_hints)
                auxiliary = [dict(row) for row in client.search_memory(fallback_query, limit=query.limit)]
                rows = merge_entity_rows(rows, auxiliary, intent.entity_hints)
                hits = grounded_rows(fallback_query, rows)
    except (OSError, ValueError, McpUnreachableError, McpFatalError) as error:
        return RagFetch((), "unavailable", (f"rag 계층 불가({error.__class__.__name__})",))
    items = tuple(item_from_rag(row, grounded) for row, grounded in hits)
    return RagFetch(items, "hit" if items else "no_memory", ())

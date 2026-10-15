"""recall-v1 response assembly with shared grounding."""

from __future__ import annotations

from typing import Any

from automation.knowledge import core as knowledge_core

SCHEMA_VERSION = "recall-v1"
STATUS_HIT = "hit"
STATUS_NO_MEMORY = "no_memory"
STATUS_UNAVAILABLE = "unavailable"
NO_MEMORY_MESSAGE = "기억 없음"
UNAVAILABLE_MESSAGE = "검색 불가 — RAG 메모리 노드에 연결할 수 없습니다. 재시도하지 않고 일반 지식으로만 답합니다."
DEFAULT_THRESHOLD = knowledge_core.DEFAULT_THRESHOLD
DEFAULT_STRONG_THRESHOLD = knowledge_core.DEFAULT_STRONG_THRESHOLD
GROUNDING_RATIO = knowledge_core.GROUNDING_RATIO
EXCERPT_CHARS = 200
_METADATA_KEYS = (
    "source_type", "title", "path", "task_id", "message_id", "session_id", "created",
    "updated", "day", "folder", "channel", "first_message_id",
    "last_message_id", "report_agent", "agent_id", "role", "project", "interest_tags", "tags",
)

EntityIntent = knowledge_core.EntityIntent
analyze_entity_intent = knowledge_core.analyze_entity_intent
merge_entity_rows = knowledge_core.merge_entity_rows
tokenize = knowledge_core.tokenize
grounding_ratio = knowledge_core.grounding_ratio


def attribution(source: str, metadata: dict[str, Any]) -> str:
    source_type = str(metadata.get("source_type", "")) or source.split(":", 1)[0]
    body = source.split(":", 1)[1] if ":" in source else source
    body = body.split("#c", 1)[0]
    path = str(metadata.get("path", "")) or body
    if source_type == "wiki":
        return f"위키: {path}"
    if source_type == "meeting":
        return f"회의: {path}"
    if source_type == "note":
        return f"노트: {path}"
    if source_type == "obsidian":
        return f"Obsidian: {path}"
    if source_type == "peer-report":
        suffix = f" (task {metadata['task_id']})" if metadata.get("task_id") else ""
        return f"동료 보고: #agents-log 메시지 {body}{suffix}"
    if source_type == "team-chat":
        return f"팀 채팅: #{metadata.get('channel', 'team')} {body}"
    if source_type == "conversation":
        session = metadata.get("session_id", body.split(":", 1)[0])
        suffix = f" ({metadata['day']})" if metadata.get("day") else ""
        return f"대화 기록: 세션 {session}{suffix}"
    return f"출처: {source}"


def _build_result(rank: int, row: dict[str, Any], grounded: bool) -> dict[str, Any]:
    raw = row.get("metadata")
    metadata = raw if isinstance(raw, dict) else {}
    content = str(row.get("content", ""))
    source = str(row.get("source", ""))
    return {
        "rank": rank,
        "score": round(float(row.get("score", 0.0)), 6),
        "grounded": grounded,
        "source": source,
        "source_type": str(metadata.get("source_type", "")),
        "attribution": attribution(source, metadata),
        "title": str(metadata.get("title", "")),
        "excerpt": content[:EXCERPT_CHARS],
        "metadata": {key: metadata[key] for key in _METADATA_KEYS if key in metadata},
    }


def classify(query: str, rows: list[dict[str, Any]], threshold: float = DEFAULT_THRESHOLD, strong_threshold: float = DEFAULT_STRONG_THRESHOLD) -> list[dict[str, Any]]:
    return [
        _build_result(rank, row, grounded)
        for rank, (row, grounded) in enumerate(
            knowledge_core.grounded_rows(query, rows, threshold, strong_threshold), start=1
        )
    ]


def build_response(
    query: str,
    rows: list[dict[str, Any]] | None,
    *,
    error: str | None = None,
    base_url: str | None = None,
    limit: int = 5,
    duration_ms: int = 0,
    threshold: float = DEFAULT_THRESHOLD,
    strong_threshold: float = DEFAULT_STRONG_THRESHOLD,
    classification_query: str | None = None,
    searches: int = 1,
    entity_hint_count: int = 0,
    picked: bool = False,
) -> dict[str, Any]:
    if error is not None or rows is None:
        status, results, message = STATUS_UNAVAILABLE, [], UNAVAILABLE_MESSAGE
    else:
        results = (
            [_build_result(rank, row, True) for rank, row in enumerate(rows[:limit], start=1)]
            if picked
            else classify(classification_query or query, rows, threshold, strong_threshold)
        )
        status = STATUS_HIT if results else STATUS_NO_MEMORY
        message = None if results else NO_MEMORY_MESSAGE
    return {
        "version": SCHEMA_VERSION, "query": query, "status": status, "message": message,
        "threshold": threshold, "strong_threshold": strong_threshold, "results": results,
        "search": {"base_url": base_url, "limit": limit, "attempts": 1, "searches": searches,
                   "entity_hint_count": entity_hint_count, "duration_ms": duration_ms, "error": error,
                   "picked": picked},
    }


def render_text(response: dict[str, Any]) -> str:
    if response["status"] == STATUS_UNAVAILABLE:
        return f"RECALL-UNAVAILABLE {response['message']}"
    if response["status"] == STATUS_NO_MEMORY:
        return f"RECALL-NO-MEMORY {response['message']}"
    lines = [f"RECALL-HIT query={response['query']!r} hits={len(response['results'])}"]
    for result in response["results"]:
        lines.append(f"[{result['rank']}] score={result['score']:.3f} grounded={str(result['grounded']).lower()} — {result['attribution']}")
        if result["title"]:
            lines.append(f"    제목: {result['title']}")
        lines.append(f"    내용: {result['excerpt']}")
    lines.append("출처 표기용: " + " / ".join(result["attribution"] for result in response["results"]))
    return "\n".join(lines)

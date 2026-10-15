"""Client recall extensions use the real JSON-RPC/SSE parser with fake HTTP."""
from __future__ import annotations

import json

import pytest

from automation.rag_ingest.mcp_client import JsonValue, McpFatalError, McpMemoryClient


def _client(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, JsonValue],
) -> tuple[McpMemoryClient, list[dict[str, JsonValue]]]:
    requests: list[dict[str, JsonValue]] = []

    def post(
        _self: McpMemoryClient, payload: dict[str, JsonValue],
    ) -> tuple[int, dict[str, str], str]:
        requests.append(payload)
        return 200, {}, "data: " + json.dumps({"id": payload["id"], **result})

    monkeypatch.setattr(McpMemoryClient, "_post", post)
    client = McpMemoryClient("http://fake", "key")
    client._session_id = "fake-session"
    return client, requests


@pytest.mark.parametrize("extended", [False, True])
def test_search_memory_sends_only_nondefault_arguments(
    monkeypatch: pytest.MonkeyPatch, extended: bool,
) -> None:
    # Given: a connected fake server.
    client, requests = _client(monkeypatch, {"result": {"structuredContent": {"result": []}}})
    # When: default or extended note search is called.
    if extended:
        assert client.search_memory(
            "query", 100, one_per_note=True, per_note=3, exclude_notes=("wiki:a",),
        ) == []
    else:
        assert client.search_memory("query") == []
    # Then: older servers receive no new default arguments.
    expected: dict[str, JsonValue] = {"query": "query", "limit": 5}
    if extended:
        expected.update({
            "limit": 100, "one_per_note": True, "per_note": 3, "exclude_notes": ["wiki:a"],
        })
    assert requests[0]["params"] == {"name": "search_memory", "arguments": expected}


def test_core_notes_returns_typed_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the same structured response shape used by search_memory.
    notes: list[JsonValue] = [{"key": "wiki:a", "title": "Title", "text": "Body"}]
    client, requests = _client(
        monkeypatch, {"result": {"structuredContent": {"result": notes}}},
    )
    # When: core notes are requested.
    found = client.core_notes()
    # Then: the no-argument tool is used and all three string fields survive.
    assert found == notes
    assert requests[0]["params"] == {"name": "memory_core_notes", "arguments": {}}


@pytest.mark.parametrize("result", [
    {"error": {"code": -32601, "message": "Unknown tool"}},
    {"result": {"isError": True, "content": []}},
    {"result": {"structuredContent": {"result": {}}}},
    {"result": {"structuredContent": {"result": [{"key": "wiki:a", "text": 1}]}}},
])
def test_core_notes_unsupported_or_malformed_is_fatal(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, JsonValue],
) -> None:
    # Given: an older server or invalid tool result.
    client, _ = _client(monkeypatch, result)
    # When / Then: callers can fall back using the existing fatal error.
    with pytest.raises(McpFatalError):
        client.core_notes()

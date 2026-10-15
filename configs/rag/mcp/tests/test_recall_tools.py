import asyncio
import importlib
from typing import Protocol, cast

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError


class _AppModule(Protocol):
    mcp: FastMCP


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    for key, value in {
        "RAG_MCP_API_KEY": "test", "RAG_COLLECTION_NAME": "memory",
        "RAG_EMBEDDING_DIMENSION": "2", "RAG_EMBEDDING_URL": "http://embedding",
        "RAG_QDRANT_URL": "http://qdrant",
    }.items():
        monkeypatch.setenv(key, value)
    app = importlib.import_module("rag_mcp.app")
    return cast("_AppModule", cast("object", app)).mcp


@pytest.mark.parametrize("arguments", [
    {"per_note": 2},
    {"exclude_notes": ["wiki:a"]},
    {"one_per_note": True, "per_note": 0},
    {"one_per_note": True, "per_note": 4},
    {"one_per_note": True, "exclude_notes": ["x"] * 301},
    {"one_per_note": True, "exclude_notes": ["x" * 301]},
    {"one_per_note": True, "entity_anchors": []},
])
def test_search_tool_rejects_invalid_note_arguments(
    server: FastMCP, arguments: dict[str, object],
) -> None:
    # Given: an in-process MCP server, with no live connections.
    async def exercise() -> None:
        async with Client(server) as client:
            # When / Then: invalid requests fail at the tool boundary, before any store I/O.
            with pytest.raises(ToolError):
                _ = await client.call_tool("search_memory", {"query": "query", **arguments})

    asyncio.run(exercise())


class _CoreStore:
    async def core_notes(self) -> list[dict[str, str]]:
        return [{"key": "wiki:a", "title": "Title", "text": "Body"}]


def test_core_tool_returns_notes_without_arguments(
    server: FastMCP, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a store whose only operation is the read-only core-note query.
    app = importlib.import_module("rag_mcp.app")
    monkeypatch.setattr(app, "memory_store", _CoreStore())

    async def exercise() -> None:
        async with Client(server) as client:
            # When: the tool is called without arguments.
            result = await client.call_tool("memory_core_notes", {})
            # Then: the MCP response preserves the core-note row.
            assert result.structured_content == {
                "result": [{"key": "wiki:a", "title": "Title", "text": "Body"}],
            }

    asyncio.run(exercise())

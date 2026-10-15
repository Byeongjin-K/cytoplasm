import asyncio
from typing import Self

import httpx
import pytest

from rag_mcp.settings import RagSettings
from rag_mcp.store import MemoryStore

_REFRESHED_SCROLLS = 2


def _point(source: str, content: str, title: str = "") -> dict[str, object]:
    return {"id": source, "payload": {
        "chunk_id": "chunk", "document_id": source, "content": content, "source": source,
        "metadata": {"title": title}, "owner_id": "cha", "schema_version": 1,
    }}


class _FakeClient:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages: list[dict[str, object]] = pages
        self.count: int = 10
        self.requests: list[dict[str, object]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str) -> httpx.Response:
        return httpx.Response(
            200, json={"result": {"points_count": self.count}},
            request=httpx.Request("GET", f"http://test{url}"),
        )

    async def post(self, url: str, *, json: dict[str, object]) -> httpx.Response:
        self.requests.append(json)
        assert url.endswith("/points/scroll")
        return httpx.Response(
            200, json={"result": self.pages.pop(0)},
            request=httpx.Request("POST", f"http://test{url}"),
        )


def _store(
    monkeypatch: pytest.MonkeyPatch, pages: list[dict[str, object]],
) -> tuple[MemoryStore, _FakeClient]:
    client = _FakeClient(pages)

    def fake_client(_self: MemoryStore, _url: str) -> _FakeClient:
        return client

    monkeypatch.setattr(MemoryStore, "client", fake_client)
    return MemoryStore(RagSettings("key", "memory", 2, "http://embedding", "http://qdrant")), client


def test_core_rules_scroll_all_pages_and_sort_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: all four core forms, a non-core note, and out-of-order chunks across pages.
    resource = "obsidian:000_PARA/Resource/rule--012345abcdef.md"
    store, client = _store(monkeypatch, [
        {"points": [
            _point("wiki:a#c0002", "second", "ignored"),
            _point("conversation:chat#c0000", "not core"),
            _point(resource + "#c0000", "resource"),
        ], "next_page_offset": "next"},
        {"points": [
            _point("wiki:a#c0001", "first", "  Metadata   title "),
            _point("note:b#c0000", "note"),
            _point("obsidian:any/person--abc.md#c0000", "person"),
        ], "next_page_offset": None},
    ])
    # When: core notes are read without embedding or writes.
    notes = asyncio.run(store.core_notes())
    # Then: only core notes remain, lexically sorted and joined by full source order.
    assert [note["key"] for note in notes] == sorted([
        "wiki:a", "note:b", resource, "obsidian:any/person--abc.md",
    ])
    assert notes[-1] == {"key": "wiki:a", "title": "Metadata title", "text": "first\n\nsecond"}
    assert client.requests[1]["offset"] == "next"
    assert all(request["with_vector"] is False for request in client.requests)


@pytest.mark.parametrize(("content", "metadata", "expected"), [
    ("# Heading", " metadata \n title ", "metadata title"),
    ('---\ntitle: "Front matter"\n---\n# Heading', "", "Front matter"),
    ("intro\ntitle: Body title\n# Heading", "", "Body title"),
    ("intro\n# Heading   title", "", "Heading title"),
    ("x" * 100, "", "x" * 80),
    ("", "", "wiki:a"),
    ("content", "x" * 140, "x" * 120),
])
def test_core_title_fallbacks(
    monkeypatch: pytest.MonkeyPatch, content: str, metadata: str, expected: str,
) -> None:
    # Given: a first chunk with the specified title sources.
    store, _ = _store(monkeypatch, [
        {"points": [_point("wiki:a#c0000", content, metadata)]},
    ])
    # When: the note is grouped.
    notes = asyncio.run(store.core_notes())
    # Then: the highest-priority usable title is normalized and bounded.
    assert notes[0]["title"] == expected


@pytest.mark.parametrize("refresh", ["count", "ttl"])
def test_core_cache_refreshes_on_count_or_ttl(
    monkeypatch: pytest.MonkeyPatch, refresh: str,
) -> None:
    # Given: a populated cache and a controlled monotonic clock.
    now = [0.0]
    monkeypatch.setattr("rag_mcp.store.monotonic", lambda: now[0])
    store, client = _store(monkeypatch, [
        {"points": [_point("wiki:a#c0000", "old")]},
        {"points": [_point("wiki:a#c0000", "new")]},
    ])
    initial = asyncio.run(store.core_notes())
    initial[0]["text"] = "caller mutation"
    assert asyncio.run(store.core_notes())[0]["text"] == "old"
    assert len(client.requests) == 1
    if refresh == "count":
        client.count += 1
    else:
        now[0] = 600.0
    # When: the collection changes or ten minutes elapse.
    notes = asyncio.run(store.core_notes())
    # Then: a full new scan replaces the cache.
    assert notes[0]["text"] == "new"
    assert len(client.requests) == _REFRESHED_SCROLLS

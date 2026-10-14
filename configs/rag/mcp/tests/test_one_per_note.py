import asyncio
from typing import Self

import httpx
import pytest

from rag_mcp.settings import RagSettings
from rag_mcp.store import NOTE_POOL, MemoryStore

_PAYLOAD: dict[str, object] = {
    "chunk_id": "chunk",
    "metadata": {},
    "owner_id": "cha",
    "schema_version": 1,
}


def _point(source: str, score: float) -> dict[str, object]:
    return {
        "payload": {**_PAYLOAD, "content": source, "document_id": source, "source": source},
        "score": score,
    }


_DEFAULT_LIMIT = 2


class _FakeClient:
    responses: list[dict[str, object]]
    bodies: list[dict[str, object]]

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.bodies = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, object]) -> httpx.Response:
        self.bodies.append(json)
        return httpx.Response(
            200, json=self.responses.pop(0), request=httpx.Request("POST", f"http://test{url}"),
        )


def _store(
    monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, object]],
) -> tuple[MemoryStore, _FakeClient]:
    client = _FakeClient(responses)
    settings = RagSettings("key", "memory", 2, "http://embedding", "http://qdrant")

    def fake_client(_self: MemoryStore, _url: str) -> _FakeClient:
        return client

    monkeypatch.setattr(MemoryStore, "client", fake_client)
    return MemoryStore(settings), client


def _responses(*points: dict[str, object]) -> list[dict[str, object]]:
    return [{"data": [{"embedding": [0.1, 0.2]}]}, {"result": {"points": list(points)}}]


def test_one_per_note_keeps_the_best_passage_of_each_note_in_score_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client = _store(monkeypatch, _responses(
        _point("conversation:chat-a#c0001", 0.9),
        _point("conversation:chat-a#c0002", 0.8),
        _point("wiki:rule.md#c0000", 0.7),
        _point("conversation:chat-a#c0003", 0.6),
        _point("obsidian:people/kim.md#c0004", 0.5),
        _point("obsidian:other.md#c0000", 0.4),
    ))

    results = asyncio.run(store.search("질문", 3, one_per_note=True))

    assert [result.source for result in results] == [
        "conversation:chat-a#c0001", "wiki:rule.md#c0000", "obsidian:people/kim.md#c0004",
    ]
    assert client.bodies[-1]["limit"] == NOTE_POOL


def test_default_search_still_asks_qdrant_for_exactly_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client = _store(monkeypatch, _responses(
        _point("conversation:chat-a#c0001", 0.9),
        _point("conversation:chat-a#c0002", 0.8),
    ))

    results = asyncio.run(store.search("질문", _DEFAULT_LIMIT))

    assert [result.source for result in results] == [
        "conversation:chat-a#c0001", "conversation:chat-a#c0002",
    ]
    assert client.bodies[-1]["limit"] == _DEFAULT_LIMIT

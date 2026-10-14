from __future__ import annotations

from typing import Any

import pytest

from automation.knowledge import pick
from automation.knowledge.pack import KnowledgeQuery
from automation.rag_ingest.mcp_client import McpUnreachableError

QUERY = KnowledgeQuery("회의록 양식 어디 거 써?", sources=frozenset({"rag"}), limit=4, caller="memory-provider")


def _row(source: str, content: str = "본문") -> dict[str, Any]:
    return {"source": source, "content": content, "score": 0.5, "metadata": {}}


def test_prompt_numbers_candidates_and_cuts_each_excerpt() -> None:
    rows = [_row("wiki:" + "a" * 120 + ".md", "줄\n바꿈   공백 " + "가" * 400), _row("obsidian:b.md")]

    prompt = pick.build_prompt("질문", rows)

    first = prompt.split("[c001] ", 1)[1].split("\n\n", 1)[0]
    source, excerpt = first.split("\n", 1)
    assert len(source) == 80
    assert excerpt.startswith("줄 바꿈 공백 ")
    assert len(excerpt) == pick.EXCERPT_CHARS
    assert "[c002] obsidian:b.md" in prompt
    assert "질문" in prompt


def test_parse_keeps_pick_order_and_drops_bad_duplicate_and_out_of_range_ids() -> None:
    answer = '앞말 {"ids": ["c003", "c001", "c003", "c009", "x", 7, "c002", "c004", "c005"]} 뒷말'

    assert pick.parse_pick(answer, 5) == (2, 0, 1, 3)


@pytest.mark.parametrize("answer", ["아무것도 없음", '{"ids": "c001"}', "[1, 2]", "{broken"])
def test_parse_returns_none_when_the_answer_is_not_the_asked_json(answer: str) -> None:
    assert pick.parse_pick(answer, 3) is None


def test_empty_pool_never_calls_the_model() -> None:
    def refuse(_prompt: str) -> str:
        raise AssertionError("model must not be called")

    pack = pick.pick_pack(QUERY, [], refuse)

    assert pack.verdict == "no_evidence"
    assert pack.items == ()


def test_picked_notes_are_attached_in_the_model_order() -> None:
    rows = [_row("wiki:a.md", "A"), _row("wiki:b.md", "B"), _row("wiki:c.md", "C")]
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return '{"ids": ["c003", "c001"]}'

    pack = pick.pick_pack(QUERY, rows, complete)

    assert len(prompts) == 1
    assert pack.verdict == "hit"
    assert [item.content for item in pack.items] == ["C", "A"]


def test_a_garbled_answer_attaches_nothing() -> None:
    pack = pick.pick_pack(QUERY, [_row("wiki:a.md")], lambda _prompt: "모르겠습니다")

    assert pack.verdict == "no_evidence"
    assert pack.items == ()


def test_an_empty_pick_attaches_nothing() -> None:
    pack = pick.pick_pack(QUERY, [_row("wiki:a.md")], lambda _prompt: '{"ids": []}')

    assert pack.verdict == "no_evidence"


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, bool]] = []

    def search_memory(self, query: str, limit: int = 5, entity_anchors: object = None, *, one_per_note: bool = False) -> list[dict[str, Any]]:
        self.calls.append((query, limit, one_per_note))
        return [_row("wiki:a.md", "A")]


def test_the_facade_asks_for_one_passage_per_note_over_the_whole_store(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    monkeypatch.setattr("automation.knowledge.adapters.rag.memory_client", lambda _env: client)

    pack = pick.pick_evidence(QUERY, lambda _prompt: '{"ids": ["c001"]}', env={})

    assert client.calls == [(QUERY.text, pick.CANDIDATE_NOTES, True)]
    assert [item.content for item in pack.items] == ["A"]


def test_an_unreachable_store_is_unavailable_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_env: object) -> object:
        raise McpUnreachableError("refused")

    monkeypatch.setattr("automation.knowledge.adapters.rag.memory_client", broken)

    pack = pick.pick_evidence(QUERY, lambda _prompt: '{"ids": []}', env={})

    assert pack.verdict == "unavailable"

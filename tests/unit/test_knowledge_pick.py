from __future__ import annotations

from collections.abc import Collection
from typing import Any

import pytest

from automation.knowledge import pick
from automation.knowledge.pack import EvidencePack, KnowledgeQuery
from automation.rag_ingest.mcp_client import McpFatalError, McpUnreachableError

QUERY = KnowledgeQuery("회의록 양식 어디 거 써?", sources=frozenset({"rag"}), limit=4, caller="memory-provider")


def _row(source: str, content: str = "본문") -> dict[str, Any]:
    return {"source": source, "content": content, "score": 0.5, "metadata": {}}


def test_group_keeps_first_appearance_order_skips_excluded_and_counts_notes() -> None:
    rows = [_row("wiki:a.md#c0001", "a1"), _row("obsidian:b.md#c0000", "b0"), _row("wiki:a.md#c0002", "a2"),
            _row("wiki:core.md#c0000"), _row("note:c.md#c0003", "c3")]

    groups = pick.group(rows, {"wiki:core.md"}, limit=2)

    assert [(key, [row["content"] for row in rows]) for key, rows in groups] == [
        ("wiki:a.md", ["a1", "a2"]), ("obsidian:b.md", ["b0"])]


def test_clean_drops_obsidian_header_lines_and_collapses_space() -> None:
    text = ">[!info]\n>**Author**: 누군가\n>**Created**: `=dateformat(this.file.ctime)`\n담당자   내선은\n7579 이다"

    assert pick.clean(text) == "담당자 내선은 7579 이다"


def test_window_moves_to_the_rarest_query_words_and_marks_the_cut() -> None:
    filler = "평범한 문장이 이어진다. " * 40
    text = filler + "실증지 담당자 내선 번호는 7579 이다. " + filler
    groups = [("obsidian:x.md", [_row("obsidian:x.md", text)]), ("obsidian:y.md", [_row("obsidian:y.md", filler)])]

    cut = pick.window(text, pick.tokens("실증지 담당자 내선"), pick.rarity(groups))

    assert cut.startswith("…")
    assert "실증지 담당자 내선" in cut
    assert len(cut) == pick.WINDOW_CHARS + 1


def test_window_without_query_overlap_is_the_cleaned_prefix() -> None:
    text = "가나다라 " * 200

    assert pick.window(text, pick.tokens("완전히 다른 말"), {"": 1.0}) == pick.clean(text)[:pick.WINDOW_CHARS]


def test_shortlist_prompt_numbers_notes_shows_two_windows_and_searches_left() -> None:
    groups = [("wiki:" + "a" * 120 + ".md", [_row("w", "첫 조각"), _row("w", "둘째 조각"), _row("w", "셋째")]),
              ("obsidian:b.md", [_row("o", "다른 노트")])]

    prompt = pick.shortlist_prompt("질문", groups, ["질문"], 2)

    first = prompt.split("[c001] ", 1)[1].split("\n\n", 1)[0]
    source, body = first.split("\n", 1)
    assert len(source) == 80
    assert body == "첫 조각\n…\n둘째 조각"
    assert "[c002] obsidian:b.md\n다른 노트" in prompt
    assert "남은 검색: 2번" in prompt
    assert "질문" in prompt


def test_confirm_prompt_shows_the_two_passages_in_full() -> None:
    long = "사실이 뒤에 있다 " * 100 + "결론 값 42"
    prompt = pick.confirm_prompt("질문", [("wiki:a.md", [_row("w", long), _row("w", "둘째")])])

    assert "결론 값 42\n…\n둘째" in prompt


def test_parse_answer_reads_search_ids_in_order_and_rejects_other_shapes() -> None:
    assert pick.parse_answer('{"search": " 다른 말 "}', 5, 8) == ("search", "다른 말")
    assert pick.parse_answer('앞 {"ids": ["c003","c001","c003","c009","x",7,"c002"]} 뒤', 5, 2) == ("ids", [2, 0])
    for answer in ("아무것도", '{"ids": "c001"}', "[1]", "{broken"):
        assert pick.parse_answer(answer, 3, 4) == ("bad", None)


class _Store:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, frozenset[str]]] = []

    def __call__(self, text: str, exclude: Collection[str]) -> list[dict[str, Any]]:
        self.calls.append((text, frozenset(exclude)))
        return self.pages[len(self.calls) - 1] if len(self.calls) <= len(self.pages) else []


def test_choose_searches_again_then_shortlists_then_confirms_on_full_text() -> None:
    store = _Store([[_row("wiki:a.md#c0", "A")], [_row("wiki:a.md#c0", "A"), _row("obsidian:b.md#c1", "B 사실 7579")]])
    answers = iter(['{"search": "노트 말"}', '{"ids": ["c002", "c001"]}', '{"ids": ["c001"]}'])
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    chosen = pick.choose("내선 뭐였지", store, complete, exclude={"wiki:core.md"})

    assert store.calls == [("내선 뭐였지", frozenset({"wiki:core.md"})),
                           ("노트 말", frozenset({"wiki:core.md", "wiki:a.md"}))]
    assert [key for key, _rows in chosen] == ["obsidian:b.md"]
    assert "남은 검색: 2번" in prompts[0] and "남은 검색: 1번" in prompts[1]
    assert "[c001] obsidian:b.md\nB 사실 7579" in prompts[2]


def test_a_search_request_with_no_searches_left_attaches_nothing() -> None:
    store = _Store([[_row(f"wiki:{n}.md")] for n in range(3)])
    calls: list[str] = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        return '{"search": "또"}'

    assert pick.choose("질문", store, complete) == []
    assert len(store.calls) == pick.MAX_SEARCHES
    assert len(calls) == pick.MAX_SEARCHES


@pytest.mark.parametrize("confirm", ['{"ids": []}', "모르겠다"])
def test_an_empty_or_garbled_confirm_attaches_nothing(confirm: str) -> None:
    answers = iter(['{"ids": ["c001"]}', confirm])

    assert pick.choose("질문", _Store([[_row("wiki:a.md")]]), lambda _p: next(answers)) == []


def test_an_empty_store_never_calls_the_model() -> None:
    def refuse(_prompt: str) -> str:
        raise AssertionError("model must not be called")

    assert pick.choose("질문", _Store([[]]), refuse) == []


def test_attached_rows_join_two_passages_within_the_total_budget() -> None:
    big = "가" * 3000
    chosen = [("wiki:a.md", [_row("wiki:a.md#c0", big), _row("wiki:a.md#c1", "B"), _row("wiki:a.md#c2", "C")]),
              ("wiki:b.md", [_row("wiki:b.md#c0", big)]), ("wiki:c.md", [_row("wiki:c.md#c0", "X")])]

    rows = pick.attached_rows(chosen)

    assert [row["content"] for row in rows] == [big + "\n…\nB", big[:pick.ATTACH_CHARS - 3004]]
    assert sum(len(row["content"]) for row in rows) == pick.ATTACH_CHARS


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def search_memory(self, query: str, limit: int = 5, entity_anchors: object = None, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"query": query, "limit": limit, **kwargs})
        return [_row("wiki:a.md#c0", "A")]


def test_the_facade_asks_for_sixty_notes_two_passages_and_the_exclusions(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    monkeypatch.setattr("automation.knowledge.adapters.rag.memory_client", lambda _env: client)
    answers = iter(['{"ids": ["c001"]}', '{"ids": ["c001"]}'])

    pack = pick.pick_evidence(QUERY, lambda _p: next(answers), env={}, exclude={"wiki:z.md", "obsidian:y.md"})

    assert client.calls == [{"query": QUERY.text, "limit": 60, "one_per_note": True, "per_note": 2,
                             "exclude_notes": ["obsidian:y.md", "wiki:z.md"]}]
    assert pack.verdict == "hit"
    assert [item.content for item in pack.items] == ["A"]


def test_an_unreachable_store_is_unavailable_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_env: object) -> object:
        raise McpUnreachableError("refused")

    monkeypatch.setattr("automation.knowledge.adapters.rag.memory_client", broken)

    assert pick.pick_evidence(QUERY, lambda _p: '{"ids": []}', env={}).verdict == "unavailable"


def test_an_older_server_that_rejects_the_arguments_falls_back_to_the_previous_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OldServer:
        def search_memory(self, *_args: object, **_kwargs: object) -> list[dict[str, Any]]:
            raise McpFatalError("validation error")

    previous = EvidencePack("knowledge-v1", QUERY, "hit", (), {"rag": "hit"})
    monkeypatch.setattr("automation.knowledge.adapters.rag.memory_client", lambda _env: OldServer())
    monkeypatch.setattr("automation.knowledge.facade.collect_evidence", lambda query, env: previous)

    def refuse(_prompt: str) -> str:
        raise AssertionError("model must not be called")

    assert pick.pick_evidence(QUERY, refuse, env={}) is previous

from __future__ import annotations

from automation.knowledge.pack import EvidenceItem, KnowledgeQuery
from automation.knowledge.plan import analyze_query
from automation.knowledge.rank import rank_and_deduplicate


def _item(*, ref: str, store: str = "rag", source_type: str = "conversation", date: str | None = None, score: float | None = 0.8, content: str = "same") -> EvidenceItem:
    return EvidenceItem("", store, source_type, ref, ref, date, "day" if date else "none", score, True, None, None, content, "")


def test_intent_and_source_hint_are_deterministic() -> None:
    plan = analyze_query("최근 배양 결과를 인용해서 설명", "cite")
    assert plan.purpose == "cite"
    assert plan.source_hint is None
    assert analyze_query("cha라면 평소 어떻게 결정했지", "cite").purpose == "judgment"
    assert analyze_query("김민준 박사와 최근 협업", "cite").purpose == "entity"
    assert analyze_query("내 노트를 종합해 제안서를 작성", "cite").purpose == "synthesize"


def test_rank_authority_then_date_then_score_and_assigns_ids() -> None:
    ranked = rank_and_deduplicate([
        _item(ref="chat", date="2026-08-21", score=0.99, content="chat"),
        _item(ref="note", source_type="note", date="2026-08-20", score=0.2, content="note"),
        _item(ref="rule", store="wiki", source_type="wiki", date="2026-07-01", score=None, content="wiki"),
    ], limit=20)
    assert [item.ref for item in ranked] == ["rule", "note", "chat"]
    assert [item.id for item in ranked] == ["E1", "E2", "E3"]
    assert all(len(item.sha256) == 64 for item in ranked)


def test_union_deduplicates_hash_and_wiki_obsidian_alias() -> None:
    ranked = rank_and_deduplicate([
        _item(ref="wiki:shared.md#c0000", store="wiki", source_type="wiki", content="first"),
        _item(ref="obsidian:shared.md#c0001", store="obsidian", source_type="obsidian", content="second"),
        _item(ref="other", content="first"),
    ], limit=20)
    assert len(ranked) == 1
    assert ranked[0].store == "wiki"


def test_knowledge_query_rejects_contract_expansion() -> None:
    try:
        KnowledgeQuery("q", sources=frozenset({"rag", "other"}))
    except ValueError as error:
        assert "sources" in str(error)
    else:
        raise AssertionError("unknown source accepted")

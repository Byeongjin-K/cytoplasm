from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from automation.knowledge.adapters import rag as rag_adapter
from automation.knowledge.adapters.wiki import fetch_wiki
from automation.knowledge.core import grounded_rows
from automation.knowledge.facade import collect_evidence
from automation.knowledge.pack import KnowledgeQuery
from automation.knowledge.plan import QueryPlan, analyze_query
from automation.rag_ingest.mcp_client import McpFatalError
from automation.knowledge.rank import derive_doc_date, item_from_rag

_REPO = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO / "tests" / "fixtures" / "knowledge"
_NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def test_doc_date_uses_only_explicit_metadata_or_supported_path() -> None:
    assert derive_doc_date({"created": "2026-05-02T01:02:03Z"}, "x") == ("2026-05-02", "created")
    assert derive_doc_date({}, "20260818.md") == ("2026-08-18", "path")
    assert derive_doc_date({}, "meeting-about-2026.md") == (None, "none")


def test_rag_pack_does_not_store_or_forward_content_labels() -> None:
    item = item_from_rag({
        "source": "obsidian:note.md", "content": "특허 patent 기밀 검토",
        "score": 0.8, "metadata": {"source_type": "obsidian", "sensitivity": "restricted"},
    }, True)

    assert item.content == "특허 patent 기밀 검토"
    assert "sensitivity" not in asdict(item)
    assert not hasattr(item, "sensitivity")


def test_wiki_adapter_loads_all_matching_notes_without_rules() -> None:
    result = fetch_wiki(
        KnowledgeQuery("배양", purpose="judgment", sources=frozenset({"wiki", "twin"})),
        _NOW,
        {
            "WIKI_SCRIPTS": str(_REPO / "skills" / "wiki" / "scripts"),
            "WIKI_ROOT": str(_FIXTURES / "wiki_vault"),
        },
    )
    assert result.wiki_status == "hit"
    assert result.twin_status == "conflict"
    assert any("특허" in item.title for item in result.wiki_items)
    assert any("특허" in item.title for item in result.twin_items)
    assert result.notes == ()


def test_missing_wiki_scripts_is_fail_closed_unavailable() -> None:
    pack = collect_evidence(
        KnowledgeQuery("배양", sources=frozenset({"wiki", "twin"})),
        clock=lambda: _NOW,
        env={"WIKI_SCRIPTS": str(_FIXTURES / "absent")},
    )
    assert pack.verdict == "unavailable"
    assert pack.layers["wiki"] == pack.layers["twin"] == "unavailable"


def test_query_does_not_expand_requested_sources() -> None:
    query = KnowledgeQuery("배양", sources=frozenset({"rag"}))
    assert analyze_query(query.text, query.purpose).source_hint is None
    assert query.sources == frozenset({"rag"})


def test_irrelevant_semantic_top_one_still_fails_unchanged_grounding_threshold() -> None:
    rows = [{"score": 0.594784, "content": "일반 연구 계획 문서"}]

    assert grounded_rows("최근 김철수 박사와 함께 진행한 업무 협업 회의 과제 내역", rows) == []


@dataclass(frozen=True, slots=True)
class _RagConfig:
    mcp_base_url: str = "http://old-mcp"
    api_key: str = "test"


class _OldMcpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, tuple[str, ...] | None]] = []

    def search_memory(
        self,
        query: str,
        limit: int = 5,
        entity_anchors: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((query, limit, entity_anchors))
        if entity_anchors is not None:
            raise McpFatalError("entity_anchors validation isError: unexpected argument")
        return [
            {
                "content": "김철수 박사 협업 회의",
                "document_id": "doc-1",
                "metadata": {"source_type": "obsidian", "path": "회의.md"},
                "score": 0.7,
                "source": "obsidian:회의.md#c0000",
            }
        ]


def test_rag_adapter_degrades_to_old_mcp_without_losing_existing_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _OldMcpClient()
    monkeypatch.setattr(rag_adapter, "load_config", lambda _path: _RagConfig())
    monkeypatch.setattr(rag_adapter, "McpMemoryClient", lambda _url, _key: client)
    query = KnowledgeQuery(
        "최근 김철수 박사와 함께 진행한 협업 회의",
        purpose="entity",
        sources=frozenset({"rag"}),
    )

    result = rag_adapter.fetch_rag(query, QueryPlan("entity", None), {})

    assert result.status == "hit"
    assert [call[2] for call in client.calls] == [("김철수",), None]

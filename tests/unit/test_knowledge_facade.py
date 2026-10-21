from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from automation.knowledge.facade import collect_evidence
from automation.knowledge.pack import KnowledgeQuery
from automation.knowledge.testing import FakeSources

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge"
_NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def test_facade_derives_dates_reranks_and_includes_all_wiki() -> None:
    fake = FakeSources.from_fixture_dir(_FIXTURES)
    pack = collect_evidence(KnowledgeQuery("배양 실험", purpose="cite"), clock=lambda: _NOW, env=fake)
    assert pack.verdict == "hit"
    stores = [item.store for item in pack.items]
    assert stores[0] == "wiki"
    assert max(index for index, store in enumerate(stores) if store == "wiki") < stores.index("rag")
    assert any(item.doc_date == "2026-08-18" and item.date_basis == "path" for item in pack.items)
    assert any("특허" in item.title for item in pack.items)
    assert pack.notes == ()


def test_all_requested_layers_down_is_unavailable() -> None:
    fake = FakeSources(rag_error=ImportError(), wiki_error=ImportError(), twin_error=ImportError())
    pack = collect_evidence(KnowledgeQuery("q"), clock=lambda: _NOW, env=fake)
    assert pack.verdict == "unavailable"
    assert set(pack.layers.values()) == {"unavailable"}


def test_available_empty_layers_are_no_evidence() -> None:
    pack = collect_evidence(KnowledgeQuery("q"), clock=lambda: _NOW, env=FakeSources())
    assert pack.verdict == "no_evidence"
    assert pack.items == ()


def test_partial_layer_failure_cannot_claim_no_evidence() -> None:
    fake = FakeSources(rag_error=ImportError())
    pack = collect_evidence(KnowledgeQuery("q"), clock=lambda: _NOW, env=fake)
    assert pack.verdict == "unavailable"

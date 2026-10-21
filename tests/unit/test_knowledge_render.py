from __future__ import annotations

from automation.knowledge.pack import EvidenceItem, EvidencePack, KnowledgeQuery
from automation.knowledge.render import render_citations, render_verdict, validate_citations


def _pack(verdict: str = "hit") -> EvidencePack:
    query = KnowledgeQuery("배양 실험", purpose="cite")
    item = EvidenceItem("E1", "rag", "note", "lab-notes/20260818.md", "실험 기록", "2026-08-18", "path", 0.7, True, None, None, "본문", "a" * 64)
    return EvidencePack("knowledge-v1", query, verdict, (item,) if verdict == "hit" else (), {"rag": "hit" if verdict == "hit" else "no_memory", "wiki": "none", "twin": "none"}, ())


def test_render_uses_the_single_source_format() -> None:
    assert render_citations(_pack(), "sources") == "[E1] RAG/note: lab-notes/20260818.md (2026-08-18, path)"


def test_validate_removes_ids_outside_pack() -> None:
    report = validate_citations("사실 [E1] 날조 [E9]", _pack())
    assert report.text == "사실 [E1] 날조"
    assert report.cited_ids == ("E1",)
    assert report.stripped_ids == ("E9",)
    assert report.has_citations is True


def test_no_evidence_and_unavailable_wording_is_deterministic() -> None:
    no_evidence = _pack("no_evidence")
    assert render_citations(no_evidence, "sources") == "EVIDENCE: none — write '근거 없음' for any factual claim about the owner's past/notes"
    assert render_verdict(no_evidence).startswith("근거 없음 — 세 저장소에서 관련 근거를 찾지 못함(rag:no_memory, wiki:none)")
    unavailable = EvidencePack("knowledge-v1", no_evidence.query, "unavailable", (), {"rag": "unavailable", "wiki": "unavailable", "twin": "unavailable"}, ())
    assert render_verdict(unavailable).startswith("근거 수집 불가")

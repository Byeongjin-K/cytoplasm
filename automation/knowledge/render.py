"""The sole citation renderer and pack-bound citation validator."""

from __future__ import annotations

import re
from typing import Literal

from automation.knowledge.pack import CitationReport, EvidenceItem, EvidencePack

_CITATION = re.compile(r"\[E\d+\]")
_CITATION_NOUN = re.compile(r"\[(E\d+)\](으로|은|는|이|가|을|를|의|과|와|에서|에|로|도|만)(?![가-힣])")
_UNSUPPORTED_NOUN = "해당 자료"
_VOWEL_PARTICLE = {"은": "는", "이": "가", "을": "를", "과": "와", "으로": "로"}
_NO_EVIDENCE = "EVIDENCE: none — write '근거 없음' for any factual claim about the owner's past/notes"
_SOURCE_NAMES = {"meeting": "회의", "conversation": "대화", "team-chat": "팀", "peer-report": "동료보고"}


def _date(item: EvidenceItem) -> str:
    if item.doc_date is None:
        return "날짜 미상"
    if item.store == "wiki":
        return f"updated {item.doc_date}"
    if item.store == "obsidian":
        return f"{item.date_basis} {item.doc_date}"
    return f"{item.doc_date}, {item.date_basis}"


def _line(item: EvidenceItem) -> str:
    if item.store == "wiki":
        details = [item.source_type if item.source_type != "twin" else "principle"]
        if item.authority:
            details.append(f"authority={item.authority}")
        details.append(_date(item))
        return f"[{item.id}] wiki: {item.ref} ({', '.join(details)})"
    if item.store == "obsidian":
        return f"[{item.id}] Obsidian: {item.ref} ({_date(item)})"
    source_type = _SOURCE_NAMES.get(item.source_type, item.source_type)
    return f"[{item.id}] RAG/{source_type}: {item.ref} ({_date(item)})"


def render_citations(pack: EvidencePack, style: Literal["footnotes", "sources", "consult"] = "sources") -> str:
    """Render every style from one byte-stable source-line implementation."""
    if style not in {"footnotes", "sources", "consult"}:
        raise ValueError(f"unsupported citation style: {style}")
    if pack.verdict == "no_evidence":
        return _NO_EVIDENCE
    if pack.verdict == "unavailable":
        return "EVIDENCE: unavailable — 근거 수집 불가"
    return "\n".join(_line(item) for item in pack.items)


def render_verdict(pack: EvidencePack) -> str:
    rag = pack.layers.get("rag", "unavailable")
    wiki = pack.layers.get("wiki", "unavailable")
    if pack.verdict == "no_evidence":
        return f"근거 없음 — 세 저장소에서 관련 근거를 찾지 못함(rag:{rag}, wiki:{wiki})"
    if pack.verdict == "unavailable":
        return f"근거 수집 불가 — 세 저장소 조회 상태(rag:{rag}, wiki:{wiki}); 재시도하지 않고 생성을 계속함"
    return ""


def validate_citations(text: str, pack: EvidencePack) -> CitationReport:
    allowed = {item.id for item in pack.items}
    cited: list[str] = []
    stripped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        citation = match.group(0)
        item_id = citation[1:-1]
        target = cited if item_id in allowed else stripped
        if item_id not in target:
            target.append(item_id)
        return citation if item_id in allowed else ""

    def as_noun(match: re.Match[str]) -> str:
        # A marker used as the sentence's noun ("[E3]은 …") would leave a subjectless
        # sentence when deleted (2026-10-19 weekly report); keep a neutral noun instead.
        if match.group(1) in allowed:
            return match.group(0)
        if match.group(1) not in stripped:
            stripped.append(match.group(1))
        return _UNSUPPORTED_NOUN + _VOWEL_PARTICLE.get(match.group(2), match.group(2))

    cleaned = _CITATION_NOUN.sub(as_noun, text)
    cleaned = _CITATION.sub(replace, cleaned)
    cleaned = re.sub(r"[ \t]+(?=\n|$)", "", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    cleaned = re.sub(r" +(?=[.,!?。])", "", cleaned).strip()
    return CitationReport(cleaned, tuple(cited), tuple(stripped), bool(cited))

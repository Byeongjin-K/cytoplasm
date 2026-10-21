"""Deterministic knowledge-query intent and source-hint planning."""

from __future__ import annotations

import re
from dataclasses import dataclass

from automation.knowledge.pack import Purpose

_JUDGMENT = ("cha라면", "평소", "어떻게 결정", "판단 원칙", "의사결정")
_ENTITY = ("협업", "관계", "함께", "같이", "동료", "최근 누구")
_SYNTHESIZE = ("종합", "합성", "제안서", "보고서", "초안", "요약해")
_PERSON = re.compile(r"[가-힣]{2,4}\s*(?:박사|교수|대표|연구원|님|씨)")


@dataclass(frozen=True, slots=True)
class QueryPlan:
    purpose: Purpose
    source_hint: str | None


def analyze_query(text: str, purpose: Purpose = "cite") -> QueryPlan:
    """Combine the caller hint with deterministic, higher-specificity markers."""
    lowered = text.casefold()
    resolved: Purpose = purpose
    if any(marker in lowered for marker in _JUDGMENT):
        resolved = "judgment"
    elif any(marker in lowered for marker in _ENTITY) and _PERSON.search(text):
        resolved = "entity"
    elif any(marker in lowered for marker in _SYNTHESIZE):
        resolved = "synthesize"
    return QueryPlan(resolved, None)

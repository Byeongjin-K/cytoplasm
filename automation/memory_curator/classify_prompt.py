"""Deterministic classification prompt for ONE native-memory entry.

A downstream module sends the rendered prompt to the Codex OAuth tier
(:mod:`automation.codex_llm`) once per entry and parses the reply against the
``route``/``evidence``/``reason`` contract in
:mod:`automation.memory_curator.classify_model`.

Pure string building: no I/O, no clock, no randomness, no LLM call.  That is
load-bearing — the same entry must render byte-identically on every pass so a
re-classification produces no churn, and so QA can diff prompts across runs.
"""

from __future__ import annotations

from typing import Final

from .classify_model import Route
from .model import MemoryKind

#: Pinned by downstream parsers; bump only alongside the response contract.
MC_CLASSIFY_VERSION: Final = "mc-classify-v2"

#: Unambiguous entry delimiters — never appear in Hermes memory text.
ENTRY_OPEN: Final = "<<<ENTRY"
ENTRY_CLOSE: Final = "ENTRY>>>"

#: Route names come from the shared ``Route`` alias, so a route added there
#: without a definition here is a type error, and one dropped from the prompt
#: is caught by the ROUTES-coverage test.
_ROUTE_DEFINITIONS: Final[dict[Route, str]] = {
    "TWIN": "규범적 판단(결정/원칙/선호/정책) — 트윈으로 승격 대상.",
    "OPS_REFERENCE": (
        "구체적 운영 사실(경로/포트/scope/CLI 위치/동기화 절차)"
        " — 필요할 때 recall 가능."
    ),
    "PERSON_REFERENCE": (
        "사용자 본인이 아닌 다른 사람(동료·담당자·외부 인사)에 대한 사실 — 호칭·연락처·소속·직함·"
        "그 사람에게 쓰는 말투. 사람 노트로 옮겨 필요할 때 recall."
    ),
    "AGENT_RULE": (
        "USER.md 에 들어온 에이전트 작업 처리 규칙(메일·할 일·문서를 어떻게 처리하는가) — 사용자 본인이"
        " 누구인지가 아니다. MEMORY.md 로 옮긴다. MEMORY.md 항목에는 쓰지 않는다."
    ),
    "KEEP_NATIVE": (
        "신원/전역 응답 스타일/안전·승인 불변식/recall 이전에 필요한 라우팅 규칙."
    ),
    "UNCERTAIN": "혼합·모호·판단 불가.",
}

#: The response contract, in order. The downstream parser validates the LLM
#: reply against these exact keys, so prompt and parser cannot drift apart.
_SCHEMA_FIELDS: Final[dict[str, str]] = {
    "route": "<ROUTES 중 정확히 하나>",
    "evidence": "<항목 본문에서 그대로 복사한 8자 이상 인용>",
    "reason": "<그 route를 고른 근거 한 문장>",
}

SCHEMA_KEYS: Final[tuple[str, ...]] = tuple(_SCHEMA_FIELDS)

_SCHEMA_TEMPLATE: Final = (
    "{"
    + ", ".join(f'\"{key}\": \"{hint}\"' for key, hint in _SCHEMA_FIELDS.items())
    + "}"
)

_EVIDENCE_RULE: Final = (
    "EVIDENCE: VERBATIM>=8 — evidence 는 항목 본문에서 글자 그대로 복사한 8자 이상의"
    " 인용이어야 한다. 요약·의역·창작 금지."
)

_OUTPUT_RULE: Final = (
    "OUTPUT: JSON-ONLY — 위 SCHEMA 모양의 JSON 객체 하나만 출력한다. 설명 문장도,"
    " 마크다운 코드펜스도, 그 밖의 어떤 텍스트도 붙이지 않는다."
)


def render(entry_text: str, *, source_kind: MemoryKind) -> str:
    """Build the classification prompt for one native-memory entry."""
    lines: list[str] = [
        f"# {MC_CLASSIFY_VERSION} — Hermes 네이티브 메모리 항목 분류",
        "",
        "아래 항목 하나를 읽고 정확히 한 개의 route로 분류한다.",
        "",
        "ROUTES:",
    ]
    lines.extend(
        f"- {route} = {definition}" for route, definition in _ROUTE_DEFINITIONS.items()
    )
    lines.extend(
        [
            "",
            f"SOURCE_KIND: {source_kind}",
            "ENTRY (아래 두 마커 사이가 항목 본문 전체다):",
            ENTRY_OPEN,
            entry_text,
            ENTRY_CLOSE,
            "",
            f"SCHEMA: {_SCHEMA_TEMPLATE}",
            "FORBIDDEN-KEYS: confidence 를 포함해 위 세 키 외의 모든 키 (추가 키 금지).",
            _EVIDENCE_RULE,
            _OUTPUT_RULE,
        ]
    )
    return "\n".join(lines)

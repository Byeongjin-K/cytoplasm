from __future__ import annotations

import re
from dataclasses import replace
from typing import Final


from .classify_model import MOVABLE_ROUTES, EntryVerdict, VetoReason
from .model import MemoryKind

_IDENTITY_CUES: Final[tuple[str, ...]] = (
    "이름",
    "소속",
    "직함",
    "역할은",
    "연구원",
    "KIMM",
    "기계연",
    "나는",
    "박사",
    "선임",
)
_STYLE_CUES: Final[tuple[str, ...]] = (
    "한국어로",
    "존댓말",
    "말투",
    "답변은",
    "응답은",
    "간결",
    "이모지",
    "톤",
    "문체",
    "인사=",
)
_SAFETY_CUES: Final[tuple[str, ...]] = (
    "승인",
    "게이트",
    "✅",
    "⛔",
    "소유자",
    "fail-closed",
    "금지",
    "절대",
    "하지 않는다",
    "확인 없이",
    "먼저 물어",
    "권한",
)
_ROUTING_CUES: Final[tuple[str, ...]] = (
    "recall",
    "검색",
    "조회",
    "RAG",
    "위키",
    "트윈",
    "obsidian",
    "옵시디언",
    "라우팅",
    "저장할 때",
    "요청하면",
    "할 때는",
    "할 일",
    "투두",
    "todo",
    "tasks",
)
_CREDENTIAL_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)\bapi[_-]?key\b\s*[:=]"),
    re.compile(r"(?i)\bauthorization\b\s*:"),
    re.compile(r"(?i)\bsecret\b\s*[:=]"),
    re.compile(r"(?i)\b(pass(word|phrase)|passwd)\b\s*[:=]"),
    re.compile(r"-{5}BEGIN[ A-Z]+-{5}"),
    re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"),
)
_MARKER: Final = "<!-- mc-marker-"
_MIN_LLM_LENGTH: Final = 60


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _native_veto(text: str) -> VetoReason | None:
    lowered = text.casefold()
    cue_groups = (_IDENTITY_CUES, _STYLE_CUES, _SAFETY_CUES, _ROUTING_CUES)
    if any(cue.casefold() in lowered for group in cue_groups for cue in group):
        return "keep_native_rule"
    if _MARKER in text:
        return "marker"
    if len(_collapse(text)) < _MIN_LLM_LENGTH:
        return "too_short"
    return None


def pre_llm_veto(
    text: str,
    *,
    source_kind: MemoryKind,
) -> EntryVerdict | None:
    if any(pattern.search(text) is not None for pattern in _CREDENTIAL_PATTERNS):
        return EntryVerdict(
            source_kind=source_kind,
            entry_text=text,
            route="UNCERTAIN",
            evidence="",
            reason="credential",
            veto="credential",
            llm_called=False,
        )
    veto = _native_veto(text)
    if veto is not None:
        return EntryVerdict(
            source_kind=source_kind,
            entry_text=text,
            route="KEEP_NATIVE",
            evidence="",
            reason=veto,
            veto=veto,
            llm_called=False,
        )
    return None


def post_llm_veto(verdict: EntryVerdict) -> EntryVerdict:
    if verdict.route == "AGENT_RULE" and verdict.source_kind != "user":
        return replace(verdict, route="KEEP_NATIVE", evidence="", reason="agent_rule_in_memory", veto="keep_native_rule")
    if verdict.route in MOVABLE_ROUTES and _MARKER not in verdict.entry_text:
        return verdict
    veto = _native_veto(verdict.entry_text)
    if veto is not None:
        return replace(
            verdict,
            route="KEEP_NATIVE",
            evidence="",
            reason=veto,
            veto=veto,
        )
    if verdict.source_kind == "user" and verdict.route == "OPS_REFERENCE":
        return replace(
            verdict,
            route="KEEP_NATIVE",
            evidence="",
            reason="user_file",
            veto="user_file",
        )
    return verdict

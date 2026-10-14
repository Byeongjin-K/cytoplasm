"""Movable routes (PERSON_REFERENCE, AGENT_RULE) reach entries the keep-native cue veto used to freeze.

New file: owner decision Q138 (2026-10-14) moves colleague facts and handling rules out of USER.md,
but every such entry carries a cue like "선임"/"문체"/"할 일", so the pre-LLM veto kept them forever.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from automation.memory_curator.classify import classify_entries
from automation.memory_curator.model import MemoryEntry
from automation.twin_distill.llm import LlmInvocationError

_PERSON = "김가상의 호칭은 ‘선임님’이고 메일은 정중한 문체로 쓴다."
_RULE = "할 일 등록 요청은 원격 할 일 목록을 뜻하며 기본 목록은 '오늘할일'이다."


@dataclass(frozen=True, slots=True)
class _Llm:
    route: str
    evidence: str
    prompts: list[str]

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps({"route": self.route, "evidence": self.evidence, "reason": "grounded"})


@dataclass(frozen=True, slots=True)
class _Down:
    def complete(self, prompt: str) -> str:
        raise LlmInvocationError("down")


def test_a_cue_vetoed_colleague_entry_becomes_a_person_reference() -> None:
    llm = _Llm("PERSON_REFERENCE", "김가상의 호칭은", [])
    (verdict,) = classify_entries({"user": (MemoryEntry(_PERSON),)}, client=llm)
    assert (verdict.route, verdict.veto, verdict.llm_called) == ("PERSON_REFERENCE", None, True)
    assert len(llm.prompts) == 1


def test_a_handling_rule_in_user_md_becomes_an_agent_rule() -> None:
    (verdict,) = classify_entries({"user": (MemoryEntry(_RULE),)}, client=_Llm("AGENT_RULE", "기본 목록은 '오늘할일'이다", []))
    assert verdict.route == "AGENT_RULE"


def test_a_cue_vetoed_entry_judged_anything_else_stays_native() -> None:
    (verdict,) = classify_entries({"user": (MemoryEntry(_PERSON),)}, client=_Llm("TWIN", "김가상의 호칭은", []))
    assert (verdict.route, verdict.veto) == ("KEEP_NATIVE", "keep_native_rule")


def test_an_agent_rule_already_in_memory_md_stays_native() -> None:
    (verdict,) = classify_entries({"memory": (MemoryEntry(_RULE),)}, client=_Llm("AGENT_RULE", "기본 목록은 '오늘할일'이다", []))
    assert verdict.route == "KEEP_NATIVE"


def test_an_unreachable_model_keeps_the_cue_veto() -> None:
    (verdict,) = classify_entries({"user": (MemoryEntry(_PERSON),)}, client=_Down())
    assert (verdict.route, verdict.veto) == ("KEEP_NATIVE", "keep_native_rule")

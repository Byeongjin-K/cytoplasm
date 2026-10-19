"""Weekly research report in complete sentences: a clipped ~함/~임 draft is rewritten once."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("TOPICS_SCRIPTS", str(_ROOT / "skills" / "topics" / "scripts"))
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "automation" / "research_trends"))

from automation.research_trends import research_trends  # noqa: E402

TERSE = """## 주요 흐름
- 굴삭기 자율화 연구가 늘어남.
- 시뮬레이션 기반 학습이 주류임.
- 실제 현장 검증은 부족함.
- 데이터셋 공개가 필요함."""
PROSE = """## 주요 흐름
이번 주 논문들은 굴삭기 자율화를 다룬 연구가 늘어나고 있음을 보여 줍니다.
대부분의 연구는 시뮬레이션에서 정책을 학습합니다.
다만 실제 현장에서 검증한 결과는 아직 적습니다."""


def test_terse_ratio_separates_note_style_from_sentences():
    assert research_trends.terse_ratio(TERSE) == 1.0
    assert research_trends.terse_ratio(PROSE) == 0.0


def test_a_clipped_korean_draft_is_rewritten_once_into_sentences(monkeypatch):
    # Given: the Korean stage answers in clipped note style, the prose stage in sentences.
    monkeypatch.setattr(research_trends, "PROMPT_PATH", _ROOT / "prompts" / "research-trends-v1.md")
    monkeypatch.setenv("RESEARCH_TRENDS_FAKE_CODEX", TERSE)
    monkeypatch.setenv("RESEARCH_TRENDS_FAKE_PROSE", PROSE)
    calls: list[str] = []
    real = research_trends._run_llm
    monkeypatch.setattr(research_trends, "_run_llm",
                        lambda stage, topic, prompt: calls.append(stage) or real(stage, topic, prompt))
    # When
    text = research_trends._korean("excavator", (), "first pass")
    # Then: the report is the sentence version, after exactly one extra call.
    assert text == PROSE
    assert calls == ["codex", "prose"]


def test_a_sentence_draft_is_kept_without_an_extra_call(monkeypatch):
    monkeypatch.setattr(research_trends, "PROMPT_PATH", _ROOT / "prompts" / "research-trends-v1.md")
    monkeypatch.setenv("RESEARCH_TRENDS_FAKE_CODEX", PROSE)
    calls: list[str] = []
    real = research_trends._run_llm
    monkeypatch.setattr(research_trends, "_run_llm",
                        lambda stage, topic, prompt: calls.append(stage) or real(stage, topic, prompt))
    assert research_trends._korean("excavator", (), "first pass") == PROSE
    assert calls == ["codex"]

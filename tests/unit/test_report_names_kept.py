"""Report sentences keep their subject: unsupported citation nouns and paper ids never vanish."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("TOPICS_SCRIPTS", str(_ROOT / "skills" / "topics" / "scripts"))
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "automation" / "research_trends"))

from automation.knowledge.pack import EvidencePack  # noqa: E402
from automation.knowledge.render import validate_citations  # noqa: E402
from automation.research_trends import research_trends  # noqa: E402
from automation.research_trends.research_trends_core import Paper  # noqa: E402


def _empty_pack() -> EvidencePack:
    return EvidencePack(version="knowledge-v1", query=None, verdict="no_evidence", items=(), layers={})  # type: ignore[arg-type]


def test_an_unsupported_marker_used_as_a_subject_becomes_a_neutral_noun():
    # Given: the 2026-10-19 report shape — no evidence, markers used as sentence subjects.
    text = "[E3]은 동결된 비전 모델을 씁니다. [E4]는 고도 지도에서 목표를 고릅니다. 결과가 좋습니다 [E5]."
    # When
    report = validate_citations(text, _empty_pack())
    # Then: every sentence keeps a subject with the right particle, trailing markers go.
    assert report.text == "해당 자료는 동결된 비전 모델을 씁니다. 해당 자료는 고도 지도에서 목표를 고릅니다. 결과가 좋습니다."
    assert set(report.stripped_ids) == {"E3", "E4", "E5"}


def test_a_paper_id_left_in_the_report_turns_into_its_short_title():
    papers = (Paper(title="ExcaVLA: frozen vision for digging", abstract="", url="https://arxiv.org/abs/1", published=""),)
    assert research_trends.name_papers("[P1]은 굴착 전용 학습을 하지 않습니다.", papers) == "ExcaVLA은 굴착 전용 학습을 하지 않습니다."
    assert research_trends.name_papers("[P9]는 없습니다.", papers) == "[P9]는 없습니다."

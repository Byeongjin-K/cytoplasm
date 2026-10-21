"""Report sentences keep their subject: an unsupported citation used as a subject becomes a neutral noun."""
from __future__ import annotations

from automation.knowledge.pack import EvidencePack
from automation.knowledge.render import validate_citations


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


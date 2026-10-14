"""「용어 교정 위치 규칙」을 기계로 강제한다 — 산문이 아니라 코드가 진실이다.

AGENTS.md 「용어 교정 위치 규칙」(cha 지시, 2026-09-05)이 정한 세 가지를 여기서 대조한다:
교정 엔진의 사본이 automation 밖에 없을 것, 전사본을 만드는 경로가 교정을 부르지 않을 것,
그리고 산출 문서를 만드는 종류마다 채택했거나 사유와 함께 면제되어 있을 것.

면제는 소스 주석이 아니라 이 파일의 _EXEMPT 에 적는다 — 주석에 적으면 검사는 통과하고 동작은
없는 상태가 조용히 만들어진다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from automation import drive_taxonomy, term_glossary

_ROOT: Final = Path(__file__).resolve().parents[2]
#: 엔진이 사는 단 하나의 자리.
_ENGINE: Final = _ROOT / "automation/term_correction.py"
#: 자모 초성표 — 근접 교정 구현을 베끼면 반드시 따라오는 지문.
_JAMO_FINGERPRINT: Final = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_ENGINE_DEFS: Final = ("def parse_glossary", "def apply_glossary", "def prompt_hint")
#: 전사본을 만드는 경로. 여기서 교정을 부르면 증거가 고쳐진다.
_TRANSCRIPT_TREE: Final = _ROOT / "skills/speechtotext"
_CORRECTING_CALLS: Final = ("term_correction.apply(", "term_correction.correct(", "apply_glossary(")

#: 문서 종류 → 그 종류의 문서를 만들면서 교정을 거는 파일.
_ADOPTED: Final[dict[str, str]] = {
    "meeting": "skills/meeting/scripts/meeting_terms.py",
    "lifelog": "automation/plaud_sync/note.py",
    "proposal": "skills/proposal/engine/pipeline/orchestrator.py",
}
#: 공개 반출 매니페스트가 통째로 빼는 트리 — 없는 트리에서는 그 종류의 채택을 대조할 수 없다
#: (선례 `test_proposal_engine_pin.py` 의 skipif). 트리가 있는데 파일만 없으면 그대로 실패다.
_MANIFEST_EXCLUDED_TREES: Final[dict[str, str]] = {"proposal": "skills/proposal/engine"}
#: 아직 붙이지 않은 문서 종류와 그 사유. 사유 없는 면제는 없다.
_EXEMPT: Final[dict[str, str]] = {
    "transcript": "전사본은 원문이다 — 이 규칙이 교정을 금지하는 바로 그 문서",
    "audio": "녹음원본(audio)은 생성되는 산출 문서가 아니라 오디오 바이너리이므로 용어 교정 대상이 아니다 — 교정할 본문이 존재하지 않는다",
    "patent": "gate-only — 특허 산출물은 전용 게이트로만 나가고 Drive 트리에 폴더가 없다",
    "report": "주간동향은 수집한 원문을 인용만 한다 — 새로 쓰는 해석 본문이 생기면 채택한다",
    "budget": "예산 산출물은 숫자와 코드뿐이라 교정할 낱말이 없다",
    "procurement": "구매 산출물은 품명·규격을 원문 그대로 옮긴다",
    "doctype": "문서 저장은 소유자가 준 파일을 그대로 보관한다 — 새로 쓰는 본문이 없다",
    "share": "Drive 공유는 소유자가 승인한 파일을 바이트 그대로 올린다 — sha256 에 묶여 있어 본문을 고칠 수 없다",
}


def _shipped_adopted() -> dict[str, str]:
    """이 트리에 실제로 실려 있는 채택 종류 — 공개 반출본은 엔진 트리 없이 온다."""
    return {
        kind: relpath
        for kind, relpath in _ADOPTED.items()
        if kind not in _MANIFEST_EXCLUDED_TREES or (_ROOT / _MANIFEST_EXCLUDED_TREES[kind]).is_dir()
    }


def _sources(tree: Path) -> list[Path]:
    return sorted(path for path in tree.rglob("*.py") if "__pycache__" not in path.parts)


def test_the_engine_has_exactly_one_definition() -> None:
    """사본이 생기면 한쪽만 고쳐지고, 그때부터 같은 낱말이 문서마다 달라진다."""
    copies = [
        path.relative_to(_ROOT)
        for tree in (_ROOT / "skills", _ROOT / "automation")
        for path in _sources(tree)
        if path != _ENGINE
        and (
            _JAMO_FINGERPRINT in path.read_text(encoding="utf-8")
            or any(marker in path.read_text(encoding="utf-8") for marker in _ENGINE_DEFS)
        )
    ]

    assert copies == [], f"교정 엔진 사본: {copies}"


def test_the_transcript_stage_never_corrects() -> None:
    """전사본은 증거다 — 되돌릴 수 없는 치환을 거기에 새기지 않는다."""
    offenders = [
        path.relative_to(_ROOT)
        for path in _sources(_TRANSCRIPT_TREE)
        if any(call in path.read_text(encoding="utf-8") for call in _CORRECTING_CALLS)
    ]

    assert offenders == [], f"전사 경로가 교정을 부른다: {offenders}"


def test_every_adopted_document_kind_delegates_and_logs() -> None:
    for kind, relpath in _shipped_adopted().items():
        source = _ROOT / relpath
        assert source.is_file(), f"{kind}: {relpath} 이 없다"
        text = source.read_text(encoding="utf-8")
        assert "term_correction" in text, f"{kind}: 공용 엔진에 위임하지 않는다"
        assert term_glossary.folder_for(kind), f"{kind}: 참고 문서 폴더가 없다"


def test_the_logged_document_kinds_are_the_adopted_ones() -> None:
    """교정한 문서 종류가 로그에 그대로 적혀야 사후에 오탐을 그 문서로 되짚을 수 있다."""
    adopted = set(_shipped_adopted())
    recorded = {
        kind
        for kind in adopted
        for path in (_ROOT / "skills", _ROOT / "automation")
        for source in _sources(path)
        if f'document="{kind}"' in source.read_text(encoding="utf-8")
    }

    assert recorded == adopted, f"로그에 적히지 않는 종류: {adopted - recorded}"


def test_every_document_kind_is_either_adopted_or_exempt_with_a_reason() -> None:
    """새 문서 종류가 규칙을 조용히 비켜 가지 못하게 한다."""
    kinds = set(drive_taxonomy.CATEGORIES) | set(term_glossary.EXTRA_FOLDERS)

    assert kinds == set(_ADOPTED) | set(_EXEMPT), f"등록되지 않은 문서 종류: {kinds.symmetric_difference(set(_ADOPTED) | set(_EXEMPT))}"
    assert all(reason.strip() for reason in _EXEMPT.values())


def test_the_rule_points_at_the_guide_that_owns_the_procedure() -> None:
    agents = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "## 용어 교정 위치 규칙" in agents
    assert "docs/guide/용어-교정-규약.md" in agents
    assert (_ROOT / "docs/guide/용어-교정-규약.md").is_file()

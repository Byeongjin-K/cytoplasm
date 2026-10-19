"""SC-4: 다른 이름·같은 기능의 중복 자가 스킬 advisory (차단 아님).

이름 대조(SHADOWS-GOVERNED)와 콘텐츠 해시 델타는 '같은 이름'만 잡는다 — 에이전트가
`recall` 과 기능이 겹치는 **다른 이름**의 자가 스킬을 만들면 탐지가 0 이었다(cha 지적,
2026-08-28). 피해는 라우팅 비일관성과 중복 개발이다. 승인 범위와 소유자 명령은
dori-gate가 별도로 판정한다. 그래서 이것은 **보고**다 — 자가 저작의
무승인 착지는 소유자 결정(2026-08-15 옵션 B)이라 바꾸지 않는다.

판정은 stdlib 토큰 겹침(cron 은 LLM-free 유지): SKILL.md 의 description·tags 낱말을
governed live 스킬들과 대조해, containment(작은 쪽 기준 교집합 비율) ≥ 0.5 이고
겹친 낱말이 5개 이상일 때만 advisory 를 낸다. 오탐 상한은 실측으로 보정했다 —
governed 18개 상호 대조 최고치가 0.386(calendar↔coordination, 진짜 인접 도메인)이라
그 아래는 전부 무음이다(회귀: `tests/unit/test_selfskill_overlap.py`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.selfskill_audit.scan import _bundled_names, _skill_dirs

THRESHOLD: Final = 0.5
MIN_SHARED: Final = 5
_TOKEN: Final = re.compile(r"[0-9a-z가-힣]{2,}")
_DESCRIPTION: Final = re.compile(r'^description:\s*"?(.*?)"?\s*$', re.MULTILINE)
_TAGS: Final = re.compile(r"tags:\s*\[(.*?)\]")


@dataclass(frozen=True, slots=True)
class OverlapHit:
    self_name: str
    governed_name: str
    score: float
    shared: tuple[str, ...]  # 보고 한 줄에 실을 근거 낱말 표본


def description_tokens(skill_md: str) -> frozenset[str]:
    """description·tags 낱말만 — 본문 전체를 쓰면 서식 낱말이 점수를 지배한다."""
    described = _DESCRIPTION.search(skill_md)
    tagged = _TAGS.search(skill_md)
    raw = " ".join(
        part
        for part in (
            described.group(1) if described else "",
            tagged.group(1) if tagged else "",
        )
        if part
    )
    return frozenset(_TOKEN.findall(raw.lower()))


def containment(a: frozenset[str], b: frozenset[str]) -> tuple[float, tuple[str, ...]]:
    if not a or not b:
        return 0.0, ()
    shared = tuple(sorted(a & b))
    return len(shared) / min(len(a), len(b)), shared


def _read_tokens(skill_md: Path) -> frozenset[str]:
    try:
        return description_tokens(skill_md.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return frozenset()  # 읽을 수 없는 한 스킬이 advisory 전체를 죽이지 않는다


def _governed_corpus(governed_root: Path | None) -> dict[str, frozenset[str]]:
    """governed 는 심링크 팜 — `_skill_dirs` 는 심링크를 배제하므로 여기서는 entry 를
    나열하고 SKILL.md 읽기만 링크를 따라간다(`_governed_names` 와 같은 이유)."""
    if governed_root is None or not governed_root.is_dir():
        return {}
    try:
        entries = sorted(p for p in governed_root.iterdir() if not p.name.startswith("."))
    except OSError:
        return {}
    corpus = {entry.name: _read_tokens(entry / "SKILL.md") for entry in entries}
    return {name: tokens for name, tokens in corpus.items() if tokens}


def _self_corpus(skills_root: Path) -> dict[str, frozenset[str]]:
    if not skills_root.is_dir():
        return {}
    bundled = _bundled_names(skills_root)
    corpus = {
        skill_dir.name: _read_tokens(skill_dir / "SKILL.md")
        for skill_dir in _skill_dirs(skills_root)
        if skill_dir.name not in bundled
    }
    return {name: tokens for name, tokens in corpus.items() if tokens}


def find_overlaps(home: Path, governed_root: Path | None) -> tuple[OverlapHit, ...]:
    governed = _governed_corpus(governed_root)
    if not governed:
        return ()
    hits: list[OverlapHit] = []
    for self_name, self_tokens in sorted(_self_corpus(home / ".hermes" / "skills").items()):
        if self_name in governed:
            continue  # 같은 이름은 SHADOWS-GOVERNED 가 이미 잡는다 — 이중 보고 금지
        for governed_name, governed_tokens in sorted(governed.items()):
            score, shared = containment(self_tokens, governed_tokens)
            if score >= THRESHOLD and len(shared) >= MIN_SHARED:
                hits.append(
                    OverlapHit(self_name, governed_name, round(score, 3), shared[:6])
                )
    return tuple(hits)

"""SC-4 회귀: 다른 이름·같은 기능의 자가 스킬 겹침 advisory.

`test_selfskill_audit.py` 에 더하지 않고 새 파일인 이유: 기존 파일의 케이스는 FS3
리플레이 대상이 될 수 있어 케이스 추가가 금지 패턴이다 — 새 검사는 새 파일로 간다.

고정하는 것:

* **오탐 상한** — 이 repo 의 governed 스킬 18개 SKILL.md 를 상호 대조하면 advisory 가
  0건이어야 한다(임계 보정의 정의: 진짜 인접 도메인 calendar↔coordination 조차 무음).
* 기능을 베낀 다른 이름의 자가 스킬은 잡힌다(진양성), 같은 이름은 SHADOWS 소유라
  여기서 이중 보고하지 않는다.
* advisory 는 fail-soft — 루트 부재·읽기 불가가 감사 본연을 죽이지 않는다.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from automation.selfskill_audit import overlap, report

REPO_ROOT = Path(__file__).resolve().parents[2]
GOVERNED = REPO_ROOT / "skills"


def _self_skill(home: Path, name: str, skill_md: str) -> None:
    directory = home / ".hermes" / "skills" / name
    directory.mkdir(parents=True)
    _ = (directory / "SKILL.md").write_text(skill_md, encoding="utf-8")


class TestFalsePositiveCap:
    def test_a_governed_pairwise_comparison_is_silent(self) -> None:
        corpus = {
            path.parent.name: overlap.description_tokens(
                path.read_text(encoding="utf-8")
            )
            for path in sorted(GOVERNED.glob("*/SKILL.md"))
        }
        assert len(corpus) >= 18  # 코퍼스가 비어 공허하게 통과하면 안 된다
        flagged = []
        best = 0.0
        for (name_a, tokens_a), (name_b, tokens_b) in itertools.combinations(
            sorted(corpus.items()), 2
        ):
            score, shared = overlap.containment(tokens_a, tokens_b)
            best = max(best, score)
            if score >= overlap.THRESHOLD and len(shared) >= overlap.MIN_SHARED:
                flagged.append((name_a, name_b, round(score, 3)))
        assert flagged == []
        assert best < overlap.THRESHOLD  # 임계 여유(실측 최고 0.386)가 사라지면 여기서 보인다


class TestFindOverlaps:
    def test_a_renamed_copy_of_a_governed_skill_is_flagged(self, tmp_path: Path) -> None:
        recall = (GOVERNED / "recall" / "SKILL.md").read_text(encoding="utf-8")
        _self_skill(tmp_path, "mem-search", recall.replace("name: recall", "name: mem-search"))
        hits = overlap.find_overlaps(tmp_path, GOVERNED)
        assert [(h.self_name, h.governed_name) for h in hits] == [("mem-search", "recall")]
        (hit,) = hits
        assert hit.score >= overlap.THRESHOLD
        assert len(hit.shared) >= overlap.MIN_SHARED

    def test_b_same_name_is_left_to_shadows(self, tmp_path: Path) -> None:
        recall = (GOVERNED / "recall" / "SKILL.md").read_text(encoding="utf-8")
        _self_skill(tmp_path, "recall", recall)
        assert overlap.find_overlaps(tmp_path, GOVERNED) == ()

    def test_c_unrelated_self_skill_is_silent(self, tmp_path: Path) -> None:
        _self_skill(
            tmp_path,
            "haiku",
            '---\nname: haiku\ndescription: "매일 아침 계절 낱말로 하이쿠 한 수를 지어 보여준다"\n---\n',
        )
        assert overlap.find_overlaps(tmp_path, GOVERNED) == ()

    def test_d_missing_roots_yield_nothing(self, tmp_path: Path) -> None:
        assert overlap.find_overlaps(tmp_path, None) == ()
        assert overlap.find_overlaps(tmp_path, tmp_path / "absent") == ()
        recall = (GOVERNED / "recall" / "SKILL.md").read_text(encoding="utf-8")
        _self_skill(tmp_path, "mem-search", recall)
        assert overlap.find_overlaps(tmp_path / "no-home", GOVERNED) == ()

    def test_e_unreadable_skill_md_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        recall = (GOVERNED / "recall" / "SKILL.md").read_text(encoding="utf-8")
        _self_skill(tmp_path, "mem-search", recall.replace("name: recall", "name: mem-search"))
        broken = tmp_path / ".hermes" / "skills" / "broken"
        (broken / "SKILL.md").mkdir(parents=True)  # read_text → OSError
        hits = overlap.find_overlaps(tmp_path, GOVERNED)
        assert [h.self_name for h in hits] == ["mem-search"]


class TestMorningReport:
    def test_a_overlap_line_carries_guidance(self) -> None:
        hit = overlap.OverlapHit("mem-search", "recall", 0.62, ("rag", "검색", "출처"))
        text = report.render_summary((), account_label="agent", overlaps=(hit,))
        assert "OVERLAPS-GOVERNED:recall" in text
        assert "mem-search" in text
        assert "archive" in text and "승격" in text
        pending = report.render_summary((), account_label="agent", pending_overlaps=1)
        assert "미결 겹침 1건 — 승격·폐기 결정 대기(pending-overlaps.json)" in pending

    def test_b_overlaps_alone_trigger_the_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".hermes" / "skills").mkdir(parents=True)
        hit = overlap.OverlapHit("mem-search", "recall", 0.62, ("rag", "검색", "출처"))
        sent: list[str] = []
        monkeypatch.setattr(report, "_governed_root", lambda: None)
        monkeypatch.setattr(report, "find_overlaps", lambda home, root: (hit,))
        monkeypatch.delattr("automation.owner_notice.ACCEPTS_OWNER_MESSAGE")
        monkeypatch.setattr(report, "notify_owner", lambda text, **_: sent.append(text) or True)
        assert report.run_once(home=tmp_path, account_label="agent") == 0
        (text,) = sent
        assert "OVERLAPS-GOVERNED:recall" in text

    def test_c_advisory_failure_never_kills_the_audit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".hermes" / "skills").mkdir(parents=True)

        def explode(home: Path, root: Path | None) -> tuple[overlap.OverlapHit, ...]:
            raise RuntimeError("advisory broke")

        monkeypatch.setattr(report, "_governed_root", lambda: None)
        monkeypatch.setattr(report, "find_overlaps", explode)
        assert report.run_once(home=tmp_path, account_label="agent") == 0

"""Regression coverage for approval-role claims in self-authored skills."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.selfskill_audit import claims, ledger, report

REPO_ROOT = Path(__file__).resolve().parents[2]
GOVERNED = REPO_ROOT / "skills"
_NOW = datetime(2026, 9, 5, tzinfo=UTC)


def _self_skill(home: Path, name: str, skill_md: str) -> None:
    directory = home / ".hermes" / "skills" / name
    directory.mkdir(parents=True)
    _ = (directory / "SKILL.md").write_text(skill_md, encoding="utf-8")


class TestApprovalClaims:
    def test_a_release_procedure_in_a_self_skill_is_flagged(self, tmp_path: Path) -> None:
        # Given
        _self_skill(
            tmp_path,
            "release-review",
            "---\nname: release-review\n---\n[release] DO-NOT-APPROVE until the review closes.\n",
        )

        # When
        hits = claims.find_approval_claims(tmp_path)

        # Then
        assert [(hit.skill_name, hit.tokens) for hit in hits] == [
            ("release-review", ("[release]", "DO-NOT-APPROVE"))
        ]

    def test_b_canonical_approval_channel_reference_is_flagged(self, tmp_path: Path) -> None:
        # Given
        _self_skill(
            tmp_path,
            "approval-reader",
            "---\nname: approval-reader\n---\nRead #approvals before deciding.\n",
        )

        # When
        hits = claims.find_approval_claims(tmp_path)

        # Then
        assert [(hit.skill_name, hit.tokens) for hit in hits] == [
            ("approval-reader", ("#approvals",))
        ]

    def test_c_governed_skill_corpus_is_silent(self, tmp_path: Path) -> None:
        # Given
        governed_skills = sorted(GOVERNED.glob("*/SKILL.md"))
        for skill_md in governed_skills:
            _self_skill(tmp_path, skill_md.parent.name, skill_md.read_text(encoding="utf-8"))

        # When
        hits = claims.find_approval_claims(tmp_path)

        # Then
        assert len(governed_skills) >= 17
        assert hits == ()


class TestClaimsReportFailure:
    def test_a_claim_scan_failure_leaves_the_delta_summary_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given
        _self_skill(tmp_path, "agent-notes", "---\nname: agent-notes\n---\nnotes\n")
        initial = ledger.audit(tmp_path, now=_NOW)
        sent: list[str] = []

        def explode(home: Path) -> tuple[claims.ApprovalClaimHit, ...]:
            _ = home
            raise OSError

        def notify(text: str, **_: object) -> bool:
            sent.append(text)
            return True

        monkeypatch.setattr(report, "find_approval_claims", explode)
        monkeypatch.setattr(report, "_governed_root", lambda: None)
        monkeypatch.delattr("automation.owner_notice.ACCEPTS_OWNER_MESSAGE")
        monkeypatch.setattr(report, "notify_owner", notify)

        # When
        exit_code = report.run_once(home=tmp_path, account_label="agent", now=_NOW)

        # Then
        assert exit_code == 0
        assert sent == [report.render_summary(initial.pending_deltas, account_label="agent")]

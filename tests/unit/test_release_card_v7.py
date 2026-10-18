"""New v7 coverage lives here so FS3-pinned test files gain no cases."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import test_release_approval as legacy
import test_release_notes as notes_fixture
import test_release_sh as shell
from automation import release_approval, release_notes, release_plan, skill_gate
from automation.release_spec import ReleaseSpec, ReleaseSpecError, spec_from_record
from automation.release_spec_message import approval_surface_lines


def sample() -> ReleaseSpec:
    return ReleaseSpec(
        "v2.0.1", "a" * 40, "b" * 32,
        tuple((f"home:package-{index:02}", "c" * 64) for index in range(60)),
        "### runtime\n- 릴리스 승인 카드를 짧게 표시\n- 상세는 요청할 때만 조회",
        render_version=7,
    )


def test_v7_card_fits_twelve_lines_and_keeps_all_approval_facts() -> None:
    # Given: many bundles plus every supported switch family and an operator alarm.
    from automation.auto_write_policy import CARD_LINES, SURFACE_PREFIX
    from automation.notice_bot import SURFACE

    switches = (
        "dori-agent-chat:123456789", "hermes-gateways:stopped",
        "healthcheck-wrapper:node", "yoodori-channel:123456789123456789",
        "rag-mcp", SURFACE, *(SURFACE_PREFIX + kind for kind in CARD_LINES),
    )
    spec = replace(sample(), surface_digests=(
        *sample().surface_digests, *((name, "d" * 64) for name in switches),
    ), major_note="MAJOR: 운영자 조치 필요 — 기본 설치 변경")
    # When
    rendered = spec.render()
    # Then: physical lines are capped at 12; same-line switch grouping loses no notice.
    assert len(rendered.splitlines()) <= 12
    assert len(rendered) <= 1900
    assert all(value in rendered for value in (
        spec.version, spec.head_sha[:12], spec.action_hash(), spec.major_note,
        "자세한 건 유돌에게 물어보면 올려요", *approval_surface_lines(spec),
    ))
    assert "package-00" not in rendered
    assert spec.detail_messages() == ()
    assert spec.action_hash() == replace(spec, render_version=6).action_hash()


def test_v7_posts_only_one_card_and_replays_the_stored_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the real lifecycle with an offline Discord wire.
    wire = legacy._DetailWire()
    monkeypatch.setattr(release_approval, "_gate", legacy._actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)
    # When
    rc = release_approval.main(["request", "--plan-file", legacy._plan_file(tmp_path)])
    # Then: no detail POST or backlink PATCH, and the bound record replays exactly.
    record = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert record["render_version"] == "8"
    assert len(wire.posts) == 1
    assert wire.patches == []
    assert "detail_message_ids" not in record
    replay = spec_from_record(record)
    assert wire.posts[0]["content"] == replay.render()
    assert replay.bound(replay.render(), record)


def test_v7_refuses_an_over_budget_operator_notice() -> None:
    # Given / When / Then: approval facts cannot be truncated to fit.
    with pytest.raises(ReleaseSpecError, match="1900"):
        replace(sample(), major_note="x" * 1900).render()


def test_host_plan_saves_every_commit_before_returning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the actual host plan CLI and a deterministic release plan.
    commits = tuple(notes_fixture._commit(index, f"변경 {index}", ("runtime",)) for index in range(40))
    plan = notes_fixture._plan(commits)
    monkeypatch.setattr(release_plan, "build_plan", lambda *args, **kwargs: plan)
    monkeypatch.setenv("RELEASE_NOTES_DIR", str(tmp_path))
    # When
    rc = release_approval.main([
        "plan", "--repo", str(tmp_path), "--base", plan.base,
        "--head", plan.head, "--version", plan.version,
    ])
    # Then
    payload = json.loads(capsys.readouterr().out)
    saved = tmp_path / f"{plan.head}.details.md"
    assert rc == 0
    assert payload["patch_notes"] in saved.read_text()
    assert all(f"- {commit.subject} ({commit.sha12})" in saved.read_text() for commit in commits)
    assert saved.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("publish_rc", ["0", "1"])
def test_release_publishing_includes_saved_details_and_preserves_them_on_failure(
    tmp_path: Path, publish_rc: str,
) -> None:
    # Given: a real signed-tag sandbox and the same sidecar the host plan creates.
    _origin, work = shell._origin_with_commits(tmp_path)
    head = shell._git(work, "rev-parse", "HEAD")
    body = "\n".join(f"- change {index} ({index:012x})" for index in range(60))
    path = release_notes.save_detail(tmp_path / "release-notes", head, (("runtime", "c" * 64),), body)
    # When: ensure_release_note runs inside the real release shell with a fake GitHub.
    result = shell._run(tmp_path, work, decisions="0", gh_create_rc=publish_rc)
    # Then
    assert path.read_text().endswith(body + "\n")
    assert result.returncode == (0 if publish_rc == "0" else 11), result.stderr
    if publish_rc == "0":
        published = json.loads((tmp_path / "gh-state/v1.0.0.json").read_text())["body"]
        assert path.read_text() in published
        assert all(line in published for line in body.splitlines())

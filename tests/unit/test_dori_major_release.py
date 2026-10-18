"""Changing the public default is a major contract, without authorizing P5 deletion."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation.release_plan import ReleasePlanError, build_plan
from tests.unit.test_hermes_gateways_stop import _host
from tests.unit.test_release_plan import _git, _repo


def test_installer_default_change_requires_major_even_without_schema_change(tmp_path: Path) -> None:
    # Given
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    profile = repo / "automation/install/profiles.py"
    profile.parent.mkdir()
    profile.write_text('DEFAULT_PROFILE: Final = "dori"\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "change the public installer default")
    head = _git(repo, "rev-parse", "HEAD")
    # When / Then
    with pytest.raises(ReleasePlanError, match="DEFAULT_PROFILE"):
        build_plan(repo, base=base, head=head, version="v1.52.0", bump="minor")


def test_major_plan_carries_the_installer_contract_signal(tmp_path: Path) -> None:
    # Given
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    profile = repo / "automation/install/profiles.py"
    profile.parent.mkdir()
    profile.write_text('DEFAULT_PROFILE: Final = "dori"\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "change the public installer default")
    head = _git(repo, "rev-parse", "HEAD")
    # When
    plan = build_plan(repo, base=base, head=head, version="v2.0.0", bump="major")
    # Then
    assert plan.major_signals == ("automation/install/profiles.py:DEFAULT_PROFILE",)


def test_upgrading_a_stopped_node_does_not_restart_or_remove_hermes(tmp_path: Path) -> None:
    # Given: existing release harness runs the real orchestration, transport is isolated.
    repo, env = _host(tmp_path, "stopped")
    env["ACTIONS"] = "ACT|restart-gateway|agent+peer"
    # When: updated plugin declarations must not undo the release-host stop switch.
    result = subprocess.run(
        ("bash", str(repo / "automation/deploy_all.sh"), "--apply"),
        env=env, capture_output=True, text=True, check=False, timeout=20,
    )
    # Then: every attempted remote command is visible, not a permissive success mock.
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text(encoding="utf-8")
    assert "--hermes-gateways stopped" in calls
    assert "--format receipt" in calls
    assert not any(verb in calls for verb in (
        "systemctl --user restart", "systemctl --user start", "userdel",
        "cron delete", "cron remove", "rm ", "disable",
    ))

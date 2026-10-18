"""Retirement removes the peer step, never the owner/hash/review/mount checks."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from automation import skill_gate
from tests.unit import test_release_authorize as auth
from tests.unit.personal_deploy_harness import build_personal_deploy_harness


@pytest.mark.parametrize("retired,owner_pending,mounted", [
    (True, False, True), (False, False, False), (True, True, False),
])
def test_full_skill_deploy_mounts_only_with_owner_and_required_peer_step(
    tmp_path: Path, retired: bool, owner_pending: bool, mounted: bool,
) -> None:
    harness = build_personal_deploy_harness(tmp_path)
    agent = Path(harness.environment["FAKE_AGENT_HOME"])
    (agent / ".hermes/skills").unlink()  # Fixture's legacy live bind; P5 leaves a plain root.
    (agent / ".hermes/skills").mkdir()
    if retired:
        (agent / ".hermes/peer-retired").write_bytes(b"retired")
        python = agent / ".hermes/hermes-agent/venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
    env = {**harness.environment, "E2E_TEST_MODE": "", "FAKE_PEER_ATTEST_FAIL": "1",
           "FAKE_OWNER_PENDING": "1" if owner_pending else "0"}
    # Given: the independent attestor always fails. Owner approval remains a separate seam.
    from dataclasses import replace

    result = replace(harness, environment=env).deploy()
    # Then: only retired + owner approved may reach the real install invocation.
    assert (harness.skill_store / "live/demo").is_symlink() == mounted, result.stdout + result.stderr
    assert (result.returncode == 0) == mounted, result.stdout + result.stderr


@pytest.mark.parametrize("retired,owner,expected", [
    (True, True, 0), (False, True, 7), (True, False, 1),
])
def test_real_release_gate_requires_peer_unless_deployed_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retired: bool, owner: bool, expected: int,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: [{"id": auth._OWNER, "bot": False}] if owner else [],
        auth._CANCEL: [],
    })
    if retired:
        (tmp_path / ".hermes").mkdir()
        (tmp_path / ".hermes/peer-retired").write_bytes(b"retired")
    calls: list[str] = []
    monkeypatch.setattr(skill_gate, "_release_attestation_present",
                        lambda *args: calls.append("peer") or False)
    result = skill_gate.cmd_release_authorize(argparse.Namespace(
        skill="meeting", hash=auth._DIGEST, peer_attest_mode="discord",
    ))
    assert result == expected
    assert bool(calls) == (owner and not retired)

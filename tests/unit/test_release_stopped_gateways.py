"""The reconciler retains an applied stop both across upgrade and failed-smoke rollback."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation import deploy_reconcile_cli as cli
from automation.hermes_gateways import stopped_in_receipt
from automation.release_rollback import FailedReleasePhase, load_failed_release
from tests.unit.test_release_rollback import (
    _PRIOR_SHA, _TARGET_SHA, _FakeCommands, _apply, _runtime,
)


def _receipt(path: Path, mode: str = "stopped") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 2, "release_sha": _PRIOR_SHA, "hermes_gateways": mode,
        "surfaces": {"gateway_services": {"agent": mode, "peer": mode}},
    }), encoding="utf-8")


@pytest.mark.parametrize("smoke_code", (0, 23))
def test_upgrade_and_rollback_never_restart_a_previously_stopped_pair(
    tmp_path: Path, smoke_code: int,
) -> None:
    # Given
    runtime = replace(_runtime(tmp_path), gateways_stopped=True)
    fake = _FakeCommands(runtime, restart_codes=[], smoke_code=smoke_code)
    # When
    result = _apply(runtime, fake)
    # Then
    assert (result == 0) == (smoke_code == 0)
    assert not any(str(runtime.gateway_helper) in call for call in fake.calls)
    assert ("/usr/bin/bash", str(runtime.smoke_script), "--gateways-stopped") in fake.calls
    assert runtime.current.resolve().name == (_TARGET_SHA if smoke_code == 0 else _PRIOR_SHA)
    if smoke_code:
        failed = load_failed_release(runtime.failed_state)
        assert failed is not None and failed.phase is FailedReleasePhase.FAILED


@pytest.mark.parametrize("mode,expected", (("stopped", True), ("running", False)))
def test_existing_receipt_selects_the_applied_mode(tmp_path: Path, mode: str, expected: bool) -> None:
    # Given
    path = tmp_path / "receipt.json"
    _receipt(path, mode)
    # When / Then
    assert stopped_in_receipt(path, ("agent", "peer")) is expected


@pytest.mark.parametrize("payload", (
    "[]", "{}", "{", '{"hermes_gateways":"removed"}',
    '{"version":2,"hermes_gateways":"stopped","release_sha":"bad","surfaces":{}}',
))
def test_corrupt_receipt_cannot_authorize_a_restart(tmp_path: Path, payload: str) -> None:
    # Given
    path = tmp_path / "receipt.json"
    path.write_text(payload, encoding="utf-8")
    # When / Then
    with pytest.raises(ValueError):
        stopped_in_receipt(path, ("agent", "peer"))


def test_receipt_must_observe_both_configured_accounts(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "receipt.json"
    _receipt(path)
    # When / Then
    with pytest.raises(ValueError):
        stopped_in_receipt(path, ("worker", "reviewer"))


def test_cli_reads_applied_mode_before_convergence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    runtime = _runtime(tmp_path)
    fake = _FakeCommands(runtime, restart_codes=[])
    config = replace(cli._NODE_CONFIG, private_root=tmp_path / "private")
    _receipt(config.private_root / "deploy-all/receipt.json")
    monkeypatch.setattr(cli, "_NODE_CONFIG", config)
    monkeypatch.setattr(cli, "_RELEASE_RUNTIME", runtime)
    monkeypatch.setattr(cli, "run_converge", lambda _command: fake.converge())
    monkeypatch.setattr(cli, "_run_release_command", fake.run)
    monkeypatch.setattr(cli, "notify_owner", fake.notify)
    # When
    result = cli.run_release_update(_TARGET_SHA, _PRIOR_SHA)
    # Then
    assert result == 0 and fake.converge_calls == 1
    assert not any(str(runtime.gateway_helper) in call for call in fake.calls)


def test_bad_receipt_blocks_cli_before_any_effect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    runtime = _runtime(tmp_path)
    fake = _FakeCommands(runtime, restart_codes=[])
    config = replace(cli._NODE_CONFIG, private_root=tmp_path / "private")
    path = config.private_root / "deploy-all/receipt.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "_NODE_CONFIG", config)
    monkeypatch.setattr(cli, "run_converge", lambda _command: fake.converge())
    # When
    result = cli.run_release_update(_TARGET_SHA, _PRIOR_SHA)
    # Then
    assert result == 1 and fake.converge_calls == 0 and fake.calls == []


@pytest.mark.parametrize("broken", (False, True))
def test_smoke_checks_the_tree_without_demanding_a_running_gateway(tmp_path: Path, broken: bool) -> None:
    # Given
    root = Path(__file__).resolve().parents[2]
    generation = tmp_path / ("a" * 40)
    automation = generation / "automation"
    automation.mkdir(parents=True)
    (automation / "release_store.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    if broken:
        (automation / "broken.py").write_text("def unfinished(\n", encoding="utf-8")
    helper = tmp_path / "must-not-run"
    helper.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    helper.chmod(0o755)
    # When
    result = subprocess.run(
        ("bash", str(root / "automation/release-smoke.sh"), "--gateways-stopped"),
        env={**os.environ, "RELEASE_SMOKE_CURRENT": str(generation),
             "RELEASE_SMOKE_GATEWAY_HELPER": str(helper), "RELEASE_SMOKE_SUDO": ""},
        capture_output=True, text=True, check=False,
    )
    # Then
    assert (result.returncode == 0) is not broken, result.stderr

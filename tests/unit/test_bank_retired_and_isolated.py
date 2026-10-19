"""The weekly bank reports retired scenarios instead of failing on them, and keeps out of the agent home.

2026-10-19: after v2.6.2 retired report-hub, peer coordination and Hermes memory, the weekly bank
(run from the release host) failed on exactly those three scenarios, which left repair patches
blocked; its personal-memory driver also recreated an empty kanban.db in the node agent's real
~/.hermes. A scenario now names the release switch that retires it (retired_when:), and the
personal-memory driver runs its remote part with HOME inside its own push folder.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
RETIRED = {"w3-report-hub": "report-hub-off=off", "w3-coordination": "peer-retired=retired"}


def _bank(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "tests/e2e/scenarios").mkdir(parents=True)
    (root / "tests/e2e/drivers").mkdir(parents=True)
    shutil.copy(_REPO / "tests/e2e/run_bank.sh", root / "tests/e2e/run_bank.sh")
    ran = tmp_path / "ran"
    driver = root / "tests/e2e/drivers/fake.sh"
    driver.write_text(f"#!/bin/bash\ntouch {ran}\nexit 1\n")
    (root / "tests/e2e/scenarios/w3-report-hub.yaml").write_text(
        "id: w3-report-hub\ndriver: tests/e2e/drivers/fake.sh\nretired_when: report-hub-off=off\n")
    return root, ran


def _run(root: Path, flags: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "BANK_RELEASE_FLAGS_DIR": str(flags), "HOME": str(root)}
    return subprocess.run(["bash", str(root / "tests/e2e/run_bank.sh"), "--all", "--report-dir", str(root / "r")],
                          env=env, capture_output=True, text=True, timeout=60, check=False)


@pytest.mark.parametrize(("value", "retired"), [(b"off", True), (b"on", False), (b"off\n", False)])
def test_a_scenario_retired_by_its_release_switch_is_reported_not_run(
    tmp_path: Path, value: bytes, retired: bool,
) -> None:
    root, ran = _bank(tmp_path)
    flags = tmp_path / "flags"
    flags.mkdir()
    (flags / "report-hub-off").write_bytes(value)
    result = _run(root, flags)
    if retired:
        assert result.returncode == 0 and "RETIRED w3-report-hub (report-hub-off)" in result.stdout
        assert not ran.exists()
    else:
        assert result.returncode == 1 and ran.exists()


def test_without_the_switch_file_the_scenario_runs(tmp_path: Path) -> None:
    root, ran = _bank(tmp_path)
    result = _run(root, tmp_path / "absent")
    assert result.returncode == 1 and ran.exists()


@pytest.mark.parametrize(("scenario", "retired_when"), sorted(RETIRED.items()))
def test_retired_scenarios_name_their_switch(scenario: str, retired_when: str) -> None:
    text = (_REPO / f"tests/e2e/scenarios/{scenario}.yaml").read_text(encoding="utf-8")
    assert f"\nretired_when: {retired_when}" in text


def test_the_personal_memory_driver_runs_its_remote_part_outside_the_agent_home() -> None:
    driver = (_REPO / "tests/e2e/drivers/w2_personal_memory.sh").read_text(encoding="utf-8")
    assert "export HOME=$PUSH_DIR/home; cd ~; E2E_TEST_MODE=1 python3 $PUSH_DIR/remote.py" in driver

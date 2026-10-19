"""Pins conftest's node-state isolation so a node and a workstation run the same suite.

On 2026-10-19 the release host became the primary node itself. main had passed on a
workstation where /srv/autophagy-*, /etc/autophagy and the service accounts do not exist;
on the node 324 unit tests failed because production defaults resolved to the real live
mount, node config, mirror and locks. These checks simulate the node side with temporary
files, so they fail on any host when an isolation layer is removed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import unit_node

from automation import node_config, skill_mount

_REPO = Path(__file__).resolve().parents[2]
_ROOT = Path(os.environ["AUTOPHAGY_UNIT_NODE_ROOT"])


def _conftest():
    return next(
        module for module in sys.modules.values()
        if getattr(module, "__file__", None) == str(Path(__file__).with_name("conftest.py"))
    )


def test_every_redirect_points_under_the_unit_root_and_is_absent() -> None:
    # Given: the variables conftest exported for children
    env = unit_node.env()
    # Then: each path lives under the temporary unit root, and only the private TMPDIR exists
    assert env["AUTOPHAGY_REPO_ROOT"] == str(_REPO)
    for key, value in env.items():
        if key in {"AUTOPHAGY_REPO_ROOT", "AUTOPHAGY_UNIT_NODE_ROOT"}:
            continue
        assert Path(value).is_relative_to(_ROOT), key
        assert Path(value).exists() is (key == "TMPDIR"), key


def test_the_live_mount_answer_is_the_workstation_answer() -> None:
    # When: a mutating skill CLI asks whether its copy may run
    refusal = skill_mount.governed_copy_refusal("mail", _REPO / "skills/mail/scripts/triage_cli.py")
    # Then: no mount is visible, as on a host without an install
    assert skill_mount.live_root().is_relative_to(_ROOT)
    assert refusal is None


def test_in_process_node_config_is_the_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an operator HOME without an override
    monkeypatch.setenv("HOME", str(tmp_path))
    # Then: the system file conftest points at is absent, so the seed answers
    assert node_config.SYSTEM_NODE_CONFIG_PATH.is_relative_to(_ROOT)
    assert node_config.load_node_config() == node_config.default_node_config()


def _bridge(tmp_path: Path, **extra: str) -> str:
    result = subprocess.run(
        (sys.executable, "-m", "automation.node_config_sh", "--print-env"),
        cwd=_REPO, capture_output=True, text=True, check=False, timeout=30,
        env={**unit_node.env(), "HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(_REPO), **extra},
    )
    assert result.returncode == 0, result.stderr
    return next(line for line in result.stdout.splitlines() if line.startswith("NODE_PRIMARY_NODE_NAME="))


def test_the_shell_bridge_reads_the_system_file_only_where_it_is_pointed(tmp_path: Path) -> None:
    # Given: a stand-in for a node's /etc/autophagy/node.toml
    system = tmp_path / "node.toml"
    _ = system.write_text('primary_node_name = "unit-sim-node"\n', encoding="utf-8")
    seed = node_config.default_node_config().primary_node_name
    # Then: a child with the unit env sees the seed; pointed at the stand-in, the stand-in
    assert _bridge(tmp_path) == f"NODE_PRIMARY_NODE_NAME={seed}"
    assert _bridge(tmp_path, AUTOPHAGY_SYSTEM_NODE_CONFIG=str(system)) == "NODE_PRIMARY_NODE_NAME=unit-sim-node"


def test_sudo_is_refused_before_it_reaches_a_service_account() -> None:
    # When: a test runs sudo the way the node scripts do
    sudo = shutil.which("sudo")
    result = subprocess.run(("sudo", "-n", "-u", "agent", "true"), capture_output=True, text=True, check=False)
    # Then: the guard answered, not the host's sudo
    assert sudo == os.environ["AUTOPHAGY_UNIT_SUDO_GUARD"]
    assert result.returncode == 1
    assert "UNIT-TEST-SUDO-BLOCKED" in result.stderr


def test_an_open_of_an_existing_node_path_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a file standing in for node state, under a prefix the guard watches
    guard = _conftest()
    node_file = tmp_path / "srv-autophagy-private" / "state.json"
    node_file.parent.mkdir()
    _ = node_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(guard, "_NODE_PREFIXES", (str(tmp_path / "srv-autophagy"),))
    # When: code opens it, and opens a watched path that does not exist
    _ = node_file.read_text(encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        _ = (tmp_path / "srv-autophagy-absent").read_text(encoding="utf-8")
    recorded = list(guard._node_reads)
    guard._node_reads.clear()
    # Then: only the existing path is a violation (an absent one is what a workstation sees)
    assert recorded == [f"open {node_file}"]

"""The release judgment for the dori agent-chat switch runs on the node, not the release host.

v1.50.0 shipped an approved `도리 전환 켬` card that changed nothing: `deploy_all.sh` runs
`deploy_all_probe.py` over SSH, and the probe read the switch file from the node account's home,
where it never exists. It wanted `off`, saw `off`, judged the agent config current and never called
the deployer. `test_hermes_agent_profile.py` set the switch in a host HOME and observed locally, so
it could not see this. These tests keep the node shape: an empty node HOME, the mode handed over by
the host, and the shell entrypoint that must hand it over on every probe call.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation import deploy_all_config, deploy_all_probe
from automation.deploy_declarations import all_declarations
from automation.memory_provider import agent_config as agent

_REPO: Final = Path(__file__).resolve().parents[2]
_COMMAND: Final = _REPO / "automation" / "deploy_all.sh"
_EXAMPLE_CONFIG: Final = _REPO / "configs" / "node.example.toml"


def _agent_row():
    return next(row for row in all_declarations(_REPO)
                if row.account == "agent" and row.destination == ".hermes/config.yaml")


@pytest.mark.parametrize(("mode", "status"), [("on", "stale"), ("off", "ok")])
def test_node_probe_judges_with_the_mode_the_host_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, status: str,
) -> None:
    # Given: the node account's HOME has no release-host switch, and the agent config is still "off".
    monkeypatch.setenv("HOME", str(tmp_path / "node-home"))
    have = agent.wanted_identity(dori_agent_chat=False)

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        assert args[-2:] == ("--dori-agent-chat", mode)
        return 0, f"{agent.MARKER} have={have}\n"

    # When
    facts = deploy_all_config.observe_config(_agent_row(), _REPO, runner, dori_mode=mode)
    # Then: an approved cutover is stale on the node, so the deployer runs.
    assert facts[2] == status
    assert (("different-agent_chat_ignored", "-") in facts[3]) == (mode == "on")


def test_probe_cli_refuses_to_judge_without_the_host_mode() -> None:
    # Given / When / Then: no silent fallback to a switch file the node never has.
    with pytest.raises(SystemExit) as raised:
        deploy_all_probe.main(["--format", "report"])
    assert raised.value.code == 2


def _plan(tmp_path: Path, host_home: Path) -> str:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.log"
    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!/usr/bin/env bash\n"
        'cmd="${*: -1}"\n'
        'printf "%s\\n" "$cmd" >> "$FAKE_CALLS"\n'
        'if [[ "$cmd" == *"--format report"* ]]; then printf "DEPLOY-ALL: clean\\n"; exit 0; fi\n'
        "exit 97\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(host_home),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "DEPLOY_SSH_HOST": "fake-node",
        "FAKE_CALLS": str(calls),
        "HEALTHCHECK_NODE_CONFIG_PATH": str(_EXAMPLE_CONFIG),
    }
    result = subprocess.run(("bash", str(_COMMAND), "--plan"), cwd=_REPO, env=env,
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return calls.read_text(encoding="utf-8")


@pytest.mark.parametrize(("switch", "mode"), [("555", "on"), (None, "off")])
def test_deploy_all_hands_the_host_switch_to_every_probe_call(
    tmp_path: Path, switch: str | None, mode: str,
) -> None:
    # Given: the release host has (or lacks) the switch file.
    host_home = tmp_path / "host"
    if switch is not None:
        flag = host_home / ".hermes/release-flags/dori-agent-chat"
        flag.parent.mkdir(parents=True)
        flag.write_text(switch)
    host_home.mkdir(exist_ok=True)
    # When
    calls = _plan(tmp_path, host_home)
    # Then
    probes = [line for line in calls.splitlines() if "deploy_all_probe.py" in line]
    assert probes
    assert all(f"--dori-agent-chat {mode}" in line for line in probes)


def test_deploy_all_refuses_an_invalid_host_switch_before_any_probe(tmp_path: Path) -> None:
    # Given
    host_home = tmp_path / "host"
    flag = host_home / ".hermes/release-flags/dori-agent-chat"
    flag.parent.mkdir(parents=True)
    flag.write_text("555\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "ssh").write_text("#!/bin/sh\necho called >> \"$FAKE_CALLS\"\nexit 97\n")
    (fake_bin / "ssh").chmod(0o755)
    env = {**os.environ, "HOME": str(host_home), "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
           "DEPLOY_SSH_HOST": "fake-node", "FAKE_CALLS": str(tmp_path / "calls.log"),
           "HEALTHCHECK_NODE_CONFIG_PATH": str(_EXAMPLE_CONFIG)}
    # When
    result = subprocess.run(("bash", str(_COMMAND), "--plan"), cwd=_REPO, env=env,
                            capture_output=True, text=True, timeout=60, check=False)
    # Then
    assert result.returncode == 4
    assert "DORI-SWITCH-REFUSED" in result.stderr
    assert not (tmp_path / "calls.log").exists()

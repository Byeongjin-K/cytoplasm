"""Run the real deployer/config helper, replacing only transport and other bundles."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from automation import hermes_agent_chat_ignore as ignore
from automation.memory_provider import config as memory

REPO = Path(__file__).resolve().parents[2]
CHANNEL = "123456789012345678"
BOT = "555555555555555555"
HEAD = "a" * 40


def _rig(
    tmp_path: Path, switch: str | None, auth_rc: int, node_head: str = HEAD,
) -> tuple[Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    automation = repo / "automation"
    (automation / "memory_curator").mkdir(parents=True)
    for name in ("__init__.py", "hermes_agent_chat_ignore.py", "hermes_memory_config.py", "memory_provider"):
        (automation / name).symlink_to(REPO / "automation" / name)
    for name in ("memory_curator/deploy.sh", "deploy_config.sh"):
        shutil.copyfile(REPO / "automation" / name, automation / name)
    (automation / "node_config_sh.py").write_text('print("")\n')
    (automation / "deploy_provenance.sh").write_text("deploy_provenance_check() { :; }\n")
    (automation / "deploy_push.sh").write_text('push_file() { echo push >> "$CALLS"; }\n')
    (automation / "deploy_cron.sh").write_text('converge_cron() { echo cron >> "$CALLS"; }\n')
    (automation / "deploy_timer.sh").write_text('converge_timer() { echo timer >> "$CALLS"; }\n')
    authorize = automation / "release_approval_remote.sh"
    authorize.write_text(
        '#!/usr/bin/env bash\nprintf "authorize %s\\n" "$*" >> "$CALLS"\n'
        f'[[ "$*" == "authorize-dori-agent-chat --bot-id {BOT} --head {HEAD}" ]] || exit 97\n'
        f"exit {auth_rc}\n"
    )
    authorize.chmod(0o755)
    home = tmp_path / "host"
    home.mkdir()
    if switch is not None:
        flag = home / ".hermes/release-flags/dori-agent-chat"
        flag.parent.mkdir(parents=True)
        flag.write_text(switch)
    for account in ("agent", "peer"):
        root = tmp_path / "node" / account / ".hermes"
        (root / "interop").mkdir(parents=True)
        (root / "interop/config.json").write_text(json.dumps({"agent_chat_channel_id": CHANNEL}))
        (root / "config.yaml").write_text("discord:\n  ignored_channels: '44'\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, shlex, subprocess, sys\n"
        "if sys.argv[-1].startswith('readlink '):\n"
        f"    print('/srv/releases/{node_head}')\n"
        "    sys.exit(0)\n"
        "parts = shlex.split(sys.argv[-1])\n"
        "assert parts[:3] == ['sudo', '-n', '-u']\n"
        "account, script = parts[3], parts[-1]\n"
        "with open(os.environ['CALLS'], 'a') as log:\n"
        "    log.write(account + ' ' + script + '\\n')\n"
        "if 'hermes cron list' in script:\n"
        "    sys.exit(0)\n"
        "home = str(pathlib.Path(os.environ['NODE_TEST_ROOT']) / account)\n"
        "sys.exit(subprocess.run(['bash', '-c', script], env={**os.environ, 'HOME': home}).returncode)\n"
    )
    ssh.chmod(0o755)
    git = bin_dir / "git"
    git.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' '{HEAD}'\n")
    git.chmod(0o755)
    return repo, home, {
        **os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DEPLOY_SSH_HOST": "fake-node", "NODE_TEST_ROOT": str(tmp_path / "node"),
        "NODE_RELEASE_CURRENT": str(REPO), "CALLS": str(tmp_path / "calls"),
    }


@pytest.mark.parametrize("switch, auth_rc, expected, enabled", [
    (None, 97, 0, False),
    (BOT, 0, 0, True),
    (BOT, 4, 4, False),
    (BOT, 1, 4, False),
    (BOT, 8, 4, False),
    ("invalid", 0, 4, False),
])
def test_switch_deploy_requires_release_authorization_before_node_writes(
    tmp_path: Path, switch: str | None, auth_rc: int, expected: int, enabled: bool,
) -> None:
    # Given
    repo, _home, env = _rig(tmp_path, switch, auth_rc)
    path = tmp_path / "node/agent/.hermes/config.yaml"
    peer = tmp_path / "node/peer/.hermes/config.yaml"
    before = path.read_bytes()
    # When
    result = subprocess.run(
        ["bash", str(repo / "automation/memory_curator/deploy.sh")],
        env=env, capture_output=True, text=True, timeout=20, check=False,
    )
    # Then
    assert result.returncode == expected, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    if expected:
        assert path.read_bytes() == before == peer.read_bytes()
        assert not list(path.parent.glob("*.bak-*"))
        assert "push" not in calls
        assert "agent PYTHONPATH" not in calls
        assert ("DORI-SWITCH-REFUSED" if switch == "invalid" else "DORI-SWITCH-UNAUTHORIZED") in result.stderr
    else:
        mode = "on" if enabled else "off"
        assert (CHANNEL in ignore.read_ignored(path.read_text()).channels) is enabled
        assert peer.read_text() == memory.apply_profile(before.decode())
        assert f"agent PYTHONPATH={REPO} python3 -B -m automation.memory_provider.agent_config --apply --dori-agent-chat {mode}" in calls
        assert f"agent PYTHONPATH={REPO} python3 -B -m automation.memory_provider.agent_config --check --dori-agent-chat {mode}" in calls
        assert f"peer PYTHONPATH={REPO} python3 -B -m automation.memory_provider.config --apply\n" in calls
        assert ("authorize " in calls) is enabled


def test_deleting_host_switch_reverts_a_previous_cutover(tmp_path: Path) -> None:
    # Given: a previous release already applied the ignore; no flag remains.
    repo, _home, env = _rig(tmp_path, None, 97)
    path = tmp_path / "node/agent/.hermes/config.yaml"
    before = memory.apply_profile(f"discord:\n  ignored_channels: '44,{CHANNEL}'\n")
    path.write_text(before)
    # When
    result = subprocess.run(
        ["bash", str(repo / "automation/memory_curator/deploy.sh")],
        env=env, capture_output=True, text=True, timeout=20, check=False,
    )
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert path.read_text() == before.replace(f"44,{CHANNEL}", "44")


def test_approved_local_head_cannot_enable_a_different_deployed_head(tmp_path: Path) -> None:
    # Given
    repo, _home, env = _rig(tmp_path, BOT, 0, node_head="f" * 40)
    path = tmp_path / "node/agent/.hermes/config.yaml"
    before = path.read_bytes()
    # When
    result = subprocess.run(
        ["bash", str(repo / "automation/memory_curator/deploy.sh")],
        env=env, capture_output=True, text=True, timeout=20, check=False,
    )
    # Then
    assert result.returncode == 4
    assert "DORI-SWITCH-UNAUTHORIZED" in result.stderr
    assert path.read_bytes() == before
    assert not (tmp_path / "calls").exists()


@pytest.mark.parametrize("extra", [
    "--dori-agent-chat", "--dori-agent-chat maybe", "--wrong on",
    "--dori-agent-chat on trailing", "--dori-agent-chat 'on;echo unsafe'",
])
def test_helper_refuses_extra_arguments_before_transport(tmp_path: Path, extra: str) -> None:
    # Given
    script = (
        'source "$1"; run_account() { echo UNEXPECTED; }; '
        f"converge_config agent .hermes/config.yaml hermes-agent-profile-v1 {extra}"
    )
    # When
    result = subprocess.run(
        ["bash", "-c", script, "_", str(REPO / "automation/deploy_config.sh")],
        env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=10, check=False,
    )
    # Then
    assert result.returncode == 2
    assert "UNEXPECTED" not in result.stdout

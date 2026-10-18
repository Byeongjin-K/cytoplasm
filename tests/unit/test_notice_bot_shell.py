"""Drive the release shell with an isolated secret source and local SSH substitute."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_hermes_gateways_stop import _host
from tests.unit.test_notice_bot import CHANNEL, VALUE
from tests.unit.test_notice_bot_release import DIGEST


@pytest.mark.parametrize("authorized", [True, False])
def test_release_shell_installs_secret_only_after_exact_authorization(tmp_path, authorized):
    repo, env = _host(tmp_path, "stopped")
    home = Path(env["HOME"])
    flags = home / ".hermes/release-flags"
    (flags / "notice-bot").write_text("yoodori")
    (flags / "yoodori-channel").write_text(CHANNEL)
    helper = home / ".local/bin/yoodori-do"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\nprintf '%s\\n' 'DORI_DISCORD_TOKEN=" + VALUE + "'\n")
    helper.chmod(0o755)
    authorization = repo / "automation/release_approval_remote.sh"
    authorization.write_text(
        '#!/bin/bash\nprintf "authorize %s\\n" "$*" >> "$CALLS"\n'
        f'if [[ "$1" == authorize-notice-bot ]]; then exit {0 if authorized else 4}; fi\n'
    )
    authorization.chmod(0o755)
    ssh = tmp_path / "bin/ssh"
    existing = ssh.read_text()
    # The production shell and Python transport run unchanged; only the SSH endpoint
    # is local. No stdout/stderr from the secret installer is relayed by the host.
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, shlex, subprocess, sys\n"
        "parts = shlex.split(sys.argv[-1])\n"
        "if 'automation.notice_bot' in parts:\n"
        "    account = parts[parts.index('-u') + 1]\n"
        "    home = pathlib.Path(os.environ['NODE_TEST_ROOT']) / account\n"
        "    home.mkdir(parents=True, exist_ok=True)\n"
        "    args = parts[parts.index('automation.notice_bot') + 1:]\n"
        "    sys.exit(subprocess.run([sys.executable, '-m', 'automation.notice_bot', *args],\n"
        "        env={**os.environ, 'HOME':str(home)}, input=sys.stdin.buffer.read()).returncode)\n"
        "sys.exit(subprocess.run(['bash', '-c', os.environ['FALLBACK_SSH'], '_', *sys.argv[1:]]).returncode)\n"
    )
    env.update({"NODE_TEST_ROOT": str(tmp_path / "node"), "FALLBACK_SSH": existing})
    result = subprocess.run(["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == (0 if authorized else 4), result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text()
    assert f"authorize authorize-notice-bot --hash {DIGEST} --head {env['HEAD']}" in calls
    assert VALUE not in calls + result.stdout + result.stderr
    for account in ("agent", "peer", "ops"):
        path = tmp_path / "node" / account / ".hermes/notice-bot.json"
        assert path.exists() == authorized
        if authorized:
            assert json.loads(path.read_text()) == {"token": VALUE, "channel_id": CHANNEL}


def test_absent_switch_does_not_write_any_override(tmp_path):
    repo, env = _host(tmp_path, "stopped")
    result = subprocess.run(["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "automation.notice_bot install" not in (tmp_path / "calls").read_text()

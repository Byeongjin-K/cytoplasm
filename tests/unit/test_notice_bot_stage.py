"""The real release-approval archive must resolve overrides without the checkout."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.unit.test_notice_bot import installed

REPO = Path(__file__).resolve().parents[2]


def test_remote_approval_archive_contains_resolver_seed(tmp_path):
    stage, home = tmp_path / "stage", tmp_path / "home"
    stage.mkdir()
    installed(home)
    ssh = tmp_path / "ssh"
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "import os, shlex, subprocess, sys\n"
        "script = shlex.split(sys.argv[-1])[-1]\n"
        "if script.startswith('test -f'):\n"
        "    sys.exit(1)\n"
        "if 'tar -xf -' in script:\n"
        "    sys.exit(subprocess.run(['tar', '-xf', '-', '-C', os.environ['TEST_STAGE']],\n"
        "                            stdin=sys.stdin.buffer).returncode)\n"
        "sys.stdin.buffer.read()\n"
    )
    ssh.chmod(0o755)
    env = {**os.environ, "RELEASE_APPROVAL_SSH": str(ssh), "RELEASE_APPROVAL_HOST": "fake-node",
           "RELEASE_APPROVAL_HOME": str(home), "TEST_STAGE": str(stage)}
    result = subprocess.run(["bash", str(REPO / "automation/release_approval_remote.sh"),
                             "decision", "--head", "a" * 40], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (stage / "configs/node.example.toml").read_bytes() == (REPO / "configs/node.example.toml").read_bytes()
    probe = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "from automation.notice_bot import read_override; "
         "print('override-used' if read_override() else 'legacy-fallback')", str(stage)],
        env={**env, "HOME": str(home), "HEALTHCHECK_NODE_CONFIG_PATH": str(stage / "configs/node.example.toml")},
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "override-used"

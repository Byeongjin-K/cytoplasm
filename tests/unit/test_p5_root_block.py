"""Owner workstation block: syntax and read-only dry-run through fake SSH."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "docs/guide/p5-root-block.sh"


def test_owner_root_block_parses_as_bash() -> None:
    result = subprocess.run(("bash", "-n", str(SCRIPT)), capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_owner_root_block_dry_run_uses_configured_alias_without_sudo(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes/node.toml").write_text('deploy_ssh_host = "fake-node"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ssh"
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$*" > "$SSH_CALLS"\n')
    fake.chmod(0o755)
    calls = tmp_path / "ssh-calls"
    result = subprocess.run(("bash", str(SCRIPT), "--dry-run"), capture_output=True,
                            text=True, check=False, env={**os.environ, "HOME": str(home),
                            "PATH": f"{bin_dir}:{os.environ['PATH']}", "SSH_CALLS": str(calls)})
    assert result.returncode == 0, result.stderr
    assert "fake-node" in calls.read_text()
    assert "sudo" not in calls.read_text()
    assert not (home / ".hermes/retired").exists()

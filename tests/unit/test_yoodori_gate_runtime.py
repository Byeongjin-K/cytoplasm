"""Request execution wiring and local-only declared deploy smoke."""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.deploy_declarations import all_declarations
from automation.watcher_manifest import CENTRAL_MANIFEST, derive_manifest
from automation.yoodori_gate import yoodori_read_gate as gate
from tests.unit.test_yoodori_gate import ORIGIN, PY, REPO, b64


def test_calendar_creates_then_posts_only_its_returned_draft(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given
    calls: list[list[str]] = []
    def run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        assert kwargs["env"] == gate.ENV
        return SimpleNamespace(returncode=0, stdout="DRAFT-CREATED id=abc123 action=create\n")
    monkeypatch.setattr(gate.subprocess, "run", run)
    # When
    rc = gate.calendar_card([PY, "calendar_cli.py", "draft-create", "--text", "내일"],
                            gate.RELEASE, gate.ENV, gate.TIMEOUT)
    # Then
    assert rc == 0
    assert calls == [[PY, "calendar_cli.py", "draft-create", "--text", "내일"],
                     [PY, "calendar_cli.py", "post-confirm", "--draft", "abc123"]]


@pytest.mark.parametrize("stdout, rc", [
    ("DRAFT-CREATED id=../../x action=create", 0),
    ("DRAFT-CREATED id=abc123 action=create\nDRAFT-CREATED id=def456 action=create", 0),
    ("DRAFT-CREATED id=abc123 action=create", 4),
])
def test_bad_or_failed_calendar_draft_cannot_post(
    stdout: str, rc: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    calls: list[list[str]] = []
    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(returncode=rc, stdout=stdout)
    monkeypatch.setattr(gate.subprocess, "run", run)
    # When / Then
    if rc:
        assert gate.calendar_card([PY, "calendar_cli.py", "draft-create"],
                                  gate.RELEASE, gate.ENV, gate.TIMEOUT) == rc
    else:
        with pytest.raises(gate.Refused):
            gate.calendar_card([PY, "calendar_cli.py", "draft-create"],
                                gate.RELEASE, gate.ENV, gate.TIMEOUT)
    assert len(calls) == 1


def test_card_runtime_ignores_ssh_environment_and_passes_body_without_a_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given
    monkeypatch.setattr(gate, "HOME", tmp_path)
    (tmp_path / ".env.secrets").write_text(
        'DISCORD_BOT_TOKEN="private-value"\nPYTHONPATH=/untrusted\nPATH=/untrusted\n'
        "AUTOPHAGY_OWNER_ID=123\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", "/ssh-injected")
    raw = ["mail-compose", "--to", "reader@example.invalid", "--subject", "s",
           "--body-b64", b64("hello; $(id)\n<world>"), *ORIGIN]
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", shlex.join(raw))
    monkeypatch.setattr(gate, "_log", lambda *_: None)
    calls: list[list[str]] = []
    def run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        assert kwargs["env"] == {
            **gate.ENV, "DISCORD_BOT_TOKEN": "private-value", "AUTOPHAGY_OWNER_ID": "123",
            "PYTHONPATH": str(gate.RELEASE), "AUTOPHAGY_REPO_ROOT": str(gate.RELEASE),
            "AUTOPHAGY_RUNTIME_ROOT": str(gate.RELEASE),
        }
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert "shell" not in kwargs
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(gate.subprocess, "run", run)
    # When / Then
    assert gate.main() == 0
    assert len(calls) == 1
    assert calls[0][calls[0].index("--body") + 1] == "hello; $(id)\n<world>"


def test_release_declares_forced_command_without_changing_the_legacy_manifest() -> None:
    # Given / When
    rows = [r for r in all_declarations(REPO) if r.owner == "automation/yoodori_gate"]
    # Then: v2:file is sanctioned for paths outside scripts/plugins; no pattern broadening.
    assert len(rows) == 4
    row = next(r for r in rows if r.destination.endswith("yoodori-read-gate"))
    assert (row.account, row.source, row.destination, row.kind, row.policy) == (
        "agent", "automation/yoodori_gate/yoodori_read_gate.py",
        ".local/libexec/yoodori-read-gate", "file", "required")
    assert not row.legacy
    helper = next(r for r in rows if r.destination.endswith("gmail_read.py"))
    assert (helper.source, helper.kind, helper.account) == (
        "automation/yoodori_gate/gmail_read.py", "file", "agent")
    assert derive_manifest(REPO) == (REPO / CENTRAL_MANIFEST).read_text()


def test_deployer_uses_provenance_and_verified_push_locally(
    tmp_path: Path,
) -> None:
    # Given: run the real deployer and push_file, with SSH replaced by a local agent shell.
    repo = tmp_path / "repo"
    package = repo / "automation/yoodori_gate"
    package.mkdir(parents=True)
    for name in ("deploy.sh", "yoodori_read_gate.py", "gmail_read.py", "yoodori_request_runtime.py",
                 "yoodori_jobs.py"):
        (package / name).write_bytes((REPO / "automation/yoodori_gate" / name).read_bytes())
    (repo / "automation/deploy_push.sh").write_bytes((REPO / "automation/deploy_push.sh").read_bytes())
    (repo / "automation/deploy_provenance.sh").write_text(
        'deploy_provenance_check() { printf "provenance\\n"; return "${PROVENANCE_RC:-0}"; }\n')
    (repo / "automation/node_config_sh.py").write_text(
        "print('NODE_AGENT_ACCOUNT=agent')\n")
    home = tmp_path / "home"
    home.mkdir()
    bins = tmp_path / "bin"
    bins.mkdir()
    ssh = bins / "ssh"
    ssh.write_text('#!/usr/bin/env bash\nset -eu\n[[ "$1" == test-node ]] || exit 99\n'
                   'script="${2#*bash -c }"\neval "bash -c $script"\n')
    ssh.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "DEPLOY_SSH_HOST": "test-node",
           "PATH": f"{bins}:{os.environ['PATH']}"}
    # When
    proc = subprocess.run(["bash", str(package / "deploy.sh")], env=env,
                          capture_output=True, text=True, check=False)
    # Then
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dest = home / ".local/libexec/yoodori-read-gate"
    assert dest.read_bytes() == (package / "yoodori_read_gate.py").read_bytes()
    assert dest.stat().st_mode & 0o777 == 0o755
    assert dest.with_name("gmail_read.py").read_bytes() == (package / "gmail_read.py").read_bytes()
    assert (home / ".local/share/yoodori-staging").stat().st_mode & 0o777 == 0o700
    # The real sshd -I entry must import its deployed sibling, not the checkout.
    forced = subprocess.run(
        [PY, "-I", str(dest)], cwd=home,
        env={**env, "SSH_ORIGINAL_COMMAND": "send"}, capture_output=True, text=True, check=False)
    assert forced.returncode == 2
    assert "YOODORI-GATE-REFUSED" in forced.stderr
    # Given / When: failed provenance leaves the already-installed file untouched.
    before = dest.read_bytes()
    denied = subprocess.run(["bash", str(package / "deploy.sh")],
                            env={**env, "PROVENANCE_RC": "1"},
                            capture_output=True, text=True, check=False)
    # Then
    assert denied.returncode == 4
    assert dest.read_bytes() == before

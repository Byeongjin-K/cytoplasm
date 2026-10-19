"""Owner block runs in a read-only sandbox with only its temp directory writable."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from google_gate_helpers import REPO
from test_google_gate import render_args


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=False,
        env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.org",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.org"},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def inputs(tmp: Path) -> tuple[Path, list[str]]:
    repo, checkout = tmp / "repo", tmp / "fork"
    repo.mkdir()
    (repo / "automation/doctor").mkdir(parents=True)
    (repo / "docs/guide").mkdir(parents=True)
    (repo / "dori").mkdir()
    for relative in (
        "automation/dori_pin.py", "automation/doctor/guides.py",
        "docs/guide/google-gate-root-block.sh",
    ):
        shutil.copyfile(REPO / relative, repo / relative)
    shutil.copytree(REPO / "automation/google_gate", repo / "automation/google_gate",
                    ignore=shutil.ignore_patterns("__pycache__"))
    assets = checkout / "skills/dori-mode/setups/egress-gate"
    (assets / "systemd").mkdir(parents=True)
    for name in ("gate.py", "gatectl.py", "summary.py"):
        (assets / name).write_text("# synthetic fork asset\n")
    for name, script in (("dori-gate", "gate"), ("dori-gate-summary", "summary")):
        (assets / "systemd" / f"{name}.service").write_text(
            f"[Service]\nUser=dori-gate\nGroup=dori-gate\nStateDirectory=dori-gate\n"
            f"ExecStart=/usr/bin/python3 -I /opt/dori-gate/{script}.py --config /etc/dori-gate/config.json\n"
        )
    (assets / "systemd/dori-gate-summary.timer").write_text(
        "[Timer]\nOnCalendar=*-*-* 21:30:00 Asia/Seoul\n"
    )
    git(checkout, "init", "-q")
    git(checkout, "add", ".")
    git(checkout, "commit", "-qm", "test fixture")
    sha = git(checkout, "rev-parse", "HEAD")
    (repo / "dori/pin.toml").write_text(f'schema = 1\nrepo = "{checkout}"\ncommit = "{sha}"\n')
    token, client, key = tmp / "bot-input", tmp / "client-input", tmp / "relay.pub"
    token.write_text("synthetic-bot-input\n")
    client.write_text('{"installed": {}}\n')
    # A generated disposable key; only its public half is used.
    result = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp / "relay")],
                            capture_output=True, check=False)
    assert result.returncode == 0
    args = render_args()
    args[args.index("--bot-token-file") + 1] = str(token)
    args += [
        "--fork-checkout", str(checkout), "--client-secret-file", str(client),
        "--relay-key-file", str(key), "--pc-ip", "192.0.2.10",
    ]
    return repo, args


def sandbox(repo: Path, tmp: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    # Everything outside tmp is read-only, including /etc, /var and the real HOME.
    return subprocess.run([
        "bwrap", "--ro-bind", "/", "/", "--bind", str(tmp), str(tmp), "--dev", "/dev",
        "--proc", "/proc", "--chdir", str(repo), "--setenv", "TMPDIR", str(tmp),
        "--setenv", "HOME", str(tmp), "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "sh", "docs/guide/google-gate-root-block.sh", *args,
    ], env=os.environ.copy(), capture_output=True, text=True, check=False)


def test_google_gate_root_when_default_dryrun_never_writes_outside_temp(tmp_path: Path) -> None:
    # Given
    repo, args = inputs(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    # When
    result = sandbox(repo, tmp_path, args)
    # Then: a write anywhere else would fail in the read-only sandbox.
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "GOOGLE_GATE_DRYRUN_OK"
    assert result.stderr == ""
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("invalid", ["head", "ip", "secret"])
def test_google_gate_root_when_invalid_refuses_before_writes(tmp_path: Path, invalid: str) -> None:
    # Given
    repo, args = inputs(tmp_path)
    if invalid == "head":
        git(tmp_path / "fork", "commit", "--allow-empty", "-qm", "different HEAD")
    elif invalid == "ip":
        args[-1] = '192.0.2.10",no-port-forwarding'
    else:
        (tmp_path / "bot-input").write_text("")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    # When
    result = sandbox(repo, tmp_path, ["--dry-run", *args])
    # Then
    assert result.returncode == 2
    assert "GOOGLE_GATE_DRYRUN_OK" not in result.stdout
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before

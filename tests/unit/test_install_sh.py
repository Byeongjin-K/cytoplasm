"""install.sh, the Linux one line: what it trusts and what it hands to automation.install.update.

The release channel is a local bare repo whose tags are signed by a throwaway ssh key (as tests/windows/install-update.ps1
does for install.ps1); its automation.install.update only prints the argv it got, so the script's own steps are what is
checked: the fingerprint gate, skipping an unsigned newer tag, and the options passed on. bun and omo are stand-ins on PATH.
"""
from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit import unit_node

REPO = Path(__file__).resolve().parents[2]
_GIT = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false")
_FAKE_UPDATE = "import os, sys\nprint('FAKE-UPDATE', os.getcwd(), *sys.argv[1:])\n"


def _git(*args: str) -> None:
    _ = subprocess.run(("git", *_GIT, *args), check=True, capture_output=True, env={**unit_node.env(), "HOME": os.environ["HOME"]})


@pytest.fixture
def channel(tmp_path: Path) -> tuple[Path, Path, str]:
    key = tmp_path / "k"
    _ = subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "update-trust@autophagy", "-f", str(key)), check=True)
    blob = base64.b64decode(key.with_suffix(".pub").read_text(encoding="utf-8").split()[1])
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    stage, origin = tmp_path / "stage", tmp_path / "origin.git"
    for name, text in {"automation/__init__.py": "", "automation/install/__init__.py": "",
                       "automation/install/update.py": _FAKE_UPDATE}.items():
        (stage / name).parent.mkdir(parents=True, exist_ok=True)
        _ = (stage / name).write_text(text, encoding="utf-8")
    _git("init", "-q", "-b", "main", str(stage))
    _git("-C", str(stage), "add", ".")
    _git("-C", str(stage), "commit", "-q", "-m", "signed")
    _git("-C", str(stage), "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "tag", "-s", "v1.2.3", "-m", "v1.2.3")
    _git("-C", str(stage), "commit", "-q", "--allow-empty", "-m", "unsigned")
    _git("-C", str(stage), "tag", "-a", "v1.10.0", "-m", "v1.10.0")
    _git("clone", "-q", "--bare", str(stage), str(origin))
    return origin, key.with_suffix(".pub"), fingerprint


def _bin(tmp_path: Path) -> Path:
    folder = tmp_path / "bin"
    folder.mkdir()
    for tool in ("bun", "omo", "unzip"):
        _ = (folder / tool).write_text("#!/bin/sh\necho stand-in 1.3.0\n", encoding="utf-8")
        (folder / tool).chmod(0o755)
    (folder / "python3").symlink_to(sys.executable)
    return folder


def _install(tmp_path: Path, origin: Path, key: Path, fingerprint: str, **extra: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {**unit_node.env(), "HOME": str(home), "PATH": f"{_bin(tmp_path)}:/usr/bin:/bin",
           "CYTO_REPO": str(origin), "CYTO_TRUST_KEY": str(key), "CYTO_EXPECT_FINGERPRINT": fingerprint, **extra}
    script = (REPO / "install.sh").read_text(encoding="utf-8")
    return subprocess.run(("bash",), input=script, capture_output=True, text=True, env=env, check=False)


def test_the_newest_signed_release_is_installed_with_the_trust_key_and_the_roles(
    tmp_path: Path, channel: tuple[Path, Path, str]
) -> None:
    origin, key, fingerprint = channel

    done = _install(tmp_path, origin, key, fingerprint, CYTO_ROLES="okdori, moodori", CYTO_OWNER_CALL="boss")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "CYTO-UNSIGNED-SKIP v1.10.0" in done.stdout and "CYTO-RELEASE v1.2.3 (signature checked)" in done.stdout
    src = tmp_path / "home" / ".cytoplasm" / "src"
    [update] = [line.split() for line in done.stdout.splitlines() if line.startswith("FAKE-UPDATE")]
    assert update[1:] == [str(src), "--state", str(src.parent), "--trust-key", str(key), "--expect-fingerprint", fingerprint,
                          "--role", "okdori", "--role", "moodori", "--owner-call", "boss"]


def test_a_key_whose_fingerprint_is_not_the_published_one_runs_nothing_from_the_clone(
    tmp_path: Path, channel: tuple[Path, Path, str]
) -> None:
    origin, key, _ = channel

    done = _install(tmp_path, origin, key, "SHA256:" + "A" * 43)

    assert done.returncode != 0
    assert "CYTO-LINUX-FINGERPRINT-MISMATCH" in done.stderr
    assert "FAKE-UPDATE" not in done.stdout
    assert not (tmp_path / "home" / ".cytoplasm" / "src" / "automation").exists()


def test_a_second_run_updates_without_asking_for_the_key_again(tmp_path: Path, channel: tuple[Path, Path, str]) -> None:
    origin, key, fingerprint = channel
    assert _install(tmp_path, origin, key, fingerprint).returncode == 0
    _ = (tmp_path / "home" / ".cytoplasm" / "update-allowed-signers").write_text("trusted\n", encoding="utf-8")
    for tool in ("bun", "omo", "unzip", "python3"):
        (tmp_path / "bin" / tool).unlink()
    (tmp_path / "bin").rmdir()

    done = _install(tmp_path, origin, key, fingerprint)

    assert done.returncode == 0, done.stdout + done.stderr
    [update] = [line.split() for line in done.stdout.splitlines() if line.startswith("FAKE-UPDATE")]
    assert update[2:] == ["--state", str(tmp_path / "home" / ".cytoplasm")]

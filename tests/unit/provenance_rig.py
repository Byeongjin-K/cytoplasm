"""Shared rig for the deploy-provenance closure tests (RCB todo 58).

A temporary git repository holds the working-tree bytes of `automation/`, `configs/` and one small
skill plus probe deployers, committed and equal to origin/main. Real deployers run against it with
fake `ssh`/`sudo`/`hermes`; `ssh.log` counts remote calls and `dirty-helper-ran` in the node HOME
proves a dirty remote body executed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from tests.unit.cron_fixture import timer_env

REPO: Final = Path(__file__).resolve().parents[2]
MARKER: Final = "dirty-helper-ran"
PROBE_HELPER: Final = "automation/closure_probe_helper.sh"
REMOTE_HEADS: Final = {
    "automation/deploy_cron.sh": "_converge_cron_remote() {",
    "automation/deploy_tree_remote.sh": "_deploy_tree_remote_prepare() {",
    PROBE_HELPER: "probe_body() {",
    "automation/closure_probe_enter.sh": "probe_body() {",
}
_HEAD: Final = '#!/usr/bin/env bash\nset -euo pipefail\nrepo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
_CHECK: Final = 'deploy_provenance_check "$repo_root" "$repo_root/configs/node.example.toml" || exit 4\nprobe_remote\n'
_GUARD: Final = 'source "$repo_root/automation/deploy_provenance.sh"\n'
PROBES: Final = {
    "automation/closure_probe.sh": f'{_HEAD}source "$repo_root/{PROBE_HELPER}"\n{_GUARD}{_CHECK}',
    "automation/closure_probe_relative.sh":
        f'{_HEAD}{_GUARD}cd "$repo_root"\nsource {PROBE_HELPER}\ncd "$RIG"\n{_CHECK}',
    "automation/closure_probe_cd.sh": f'{_HEAD}source "$repo_root/{PROBE_HELPER}"\n{_GUARD}cd "$RIG"\n{_CHECK}',
    "automation/closure_probe_decoy.sh":
        f'{_HEAD}{_GUARD}cd "$repo_root/automation"\nsource closure_probe_helper.sh\ncd "$RIG"\n{_CHECK}',
    "closure_probe_helper.sh": "unrelated_root_function() { :; }\n",
    "automation/closure_probe_enter.sh": (
        f'{_HEAD}{_GUARD}enter() {{ cd "$RIG"; }}\nprobe_body() {{\n  true\n}}\nenter\n'
        'deploy_provenance_check "$repo_root" "$repo_root/configs/node.example.toml" || exit 4\n'
        "ssh fake-node \"sudo -n bash -c $(printf '%q' \"$(declare -f probe_body); probe_body\")\"\n"),
    PROBE_HELPER: ("probe_body() {\n  true\n}\n"
                   "probe_remote() { ssh fake-node \"sudo -n bash -c $(printf '%q' \"$(declare -f probe_body); probe_body\")\"; }\n"),
}
SENTINEL_SSH: Final = '#!/bin/bash\nprintf "%s\\n" "$1" >> "$RIG/ssh.log"\nexit 99\n'
_SSH: Final = '#!/bin/bash\nprintf "%s\\n" "$1" >> "$RIG/ssh.log"\nshift\nexec bash -c "$*"\n'
_SKIP_ENV: Final = {"PYTHONPATH", "DEPLOY_ALLOW_UNPUSHED", "DEPLOY_PROVENANCE_REF", "DEPLOY_SSH_HOST",
                    "SKILL_SRC_DIR", "BASH_ENV"}


def git(repo: Path, *args: str) -> bytes:
    identity = ("-c", "user.name=rig", "-c", "user.email=rig@example.invalid", "-c", "commit.gpgsign=false")
    return subprocess.run(("git", "-C", str(repo), *identity, *args), check=True, capture_output=True,
                          timeout=120).stdout


def build_repo(root: Path) -> Path:
    listed = git(REPO, "ls-files", "-z", "--", "automation", "configs", "skills/hello-autophagy")
    for raw in filter(None, listed.split(b"\0")):
        source = REPO / raw.decode()
        if source.is_file() and not source.is_symlink():
            (root / raw.decode()).parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copy2(source, root / raw.decode())
    for relative, text in PROBES.items():
        _ = (root / relative).write_text(text, encoding="utf-8")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "base"),
                 ("update-ref", "refs/remotes/origin/main", "HEAD")):
        _ = git(root, *args)
    return root


@contextmanager
def dirty(repo: Path, relative: str) -> Iterator[None]:
    path = repo / relative
    original = path.read_text(encoding="utf-8")
    head = REMOTE_HEADS.get(relative)
    edited = (original.replace(head, f'{head}\n  : > "$HOME/{MARKER}"', 1) if head
              else original + "# uncommitted edit\n")
    assert edited != original
    _ = path.write_text(edited, encoding="utf-8")
    try:
        yield
    finally:
        _ = path.write_text(original, encoding="utf-8")


@contextmanager
def removed(repo: Path, relative: str) -> Iterator[None]:
    path = repo / relative
    data, mode = path.read_bytes(), path.stat().st_mode
    path.unlink()
    try:
        yield
    finally:
        _ = path.write_bytes(data)
        path.chmod(mode)


def deploy(repo: Path, rig: Path, *argv: str, hermes: str = "", ssh: str = _SSH, bash_env: str = "",
           bypass: bool = False, cwd: Path | None = None) -> tuple[int, str, list[str], bool]:
    """Run `bash <argv>` from `cwd` (default the rig); return rc, stderr, ssh hosts, marker written."""
    home, bin_dir = rig / "home", rig / "bin"
    for path in (home, bin_dir):
        path.mkdir(parents=True, exist_ok=True)
    for name, body in (("ssh", ssh), ("sudo", '#!/bin/bash\nHOME="$NODE_HOME" exec bash -c "${@: -1}"\n'),
                       ("hermes", hermes or "#!/bin/bash\nexit 9\n")):
        _ = (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key not in _SKIP_ENV}
    env.update({"HOME": str(rig), "NODE_HOME": str(home), "RIG": str(rig), "REPO": str(repo),
                "PATH": f"{bin_dir}:{os.environ['PATH']}", "DEPLOY_SSH_HOST": "fake-node",
                "HEALTHCHECK_NODE_CONFIG_PATH": str(repo / "configs/node.example.toml"),
                **timer_env(rig / "timer")})
    if bash_env:
        _ = (rig / "bash_env.sh").write_text(bash_env, encoding="utf-8")
        env["BASH_ENV"] = str(rig / "bash_env.sh")
    if bypass:
        env["DEPLOY_ALLOW_UNPUSHED"] = "1"
    result = subprocess.run(("bash", *argv), env=env, cwd=cwd or rig, capture_output=True, text=True,
                            check=False, timeout=180)
    log = rig / "ssh.log"
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result.returncode, result.stderr, calls, (home / MARKER).exists()

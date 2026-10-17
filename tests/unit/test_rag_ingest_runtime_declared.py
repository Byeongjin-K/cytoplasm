"""RCB todo 15: the rag_ingest runtime copy is declared and converged under the watch lock.

Two readers import `~/.hermes/rag_ingest_runtime/rag_ingest`: the cron wrapper and the
recall search path. The copy stays; what was missing is a declaration, so a release can
see a stale copy and re-ship it. A new file because `test_rag_ingest_deploy.py` is the
legacy contract file and the deployer is driven here through fake ssh/sudo shims under a
temp HOME. Synchronisation uses a FIFO only, never a sleep.
"""
from __future__ import annotations

import hashlib
import os
import re
import select
import shlex
import subprocess
from pathlib import Path

from automation.deploy_declarations import Declaration, all_declarations
from tests.unit.cron_fixture import declared_cron, listing, timer_env

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO / "automation/rag_ingest/deploy.sh"
_SOURCE = "automation/rag_ingest"
_DEST = ".hermes/rag_ingest_runtime/rag_ingest"
_LOCK = ".hermes/rag-ingest/watch.lock"
_RUNTIME_ROW = f"agent|{_SOURCE}|{_DEST}|required"


def _declaration() -> Declaration:
    rows = [d for d in all_declarations(_REPO) if d.destination == _DEST]
    assert len(rows) == 1, rows
    return rows[0]


def _shipped_set() -> dict[str, str]:
    """What the standing probe sees: tracked *.py under the package minus top-level cron/."""
    listing = subprocess.run(
        ("git", "-C", str(_REPO / _SOURCE), "ls-files", "-z"),
        capture_output=True, check=True,
    ).stdout.decode().split("\0")
    return {
        name: hashlib.sha256((_REPO / _SOURCE / name).read_bytes()).hexdigest()
        for name in listing
        if name.endswith(".py") and not name.startswith("cron/")
    }


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def _rig(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    home, bin_dir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    shims = {
        "ssh": '#!/bin/bash\nshift\nexec bash -c "$1"\n',
        "sudo": (
            '#!/bin/bash\nwhile [[ "$1" != bash ]]; do shift; done\n'
            'shift; shift\nexec bash -c "$1"\n'
        ),
        "hermes": (
            '#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/hermes.calls"\n'
            f'[[ "$1 $2" == "cron list" ]] && printf %s {shlex.quote(listing(declared_cron("automation/rag_ingest")))}\nexit 0\n'
        ),
    }
    for name, body in shims.items():
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home),
        "DEPLOY_SSH_HOST": "fake-node",
        "DEPLOY_ALLOW_UNPUSHED": "1",
        "DEPLOY_TREE_LOCK_WAIT": "1",
        **timer_env(tmp_path / "timer"),
    }
    return home, env


def _deploy(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(_DEPLOY)), env=env, cwd=_REPO,
        capture_output=True, text=True, check=False, timeout=60,
    )


def test_the_declaration_matches_what_the_deployer_ships() -> None:
    declared = _declaration()
    assert (declared.account, declared.source, declared.kind) == ("agent", _SOURCE, "tree")
    assert declared.policy == "required"
    assert declared.attr("lock") == _LOCK
    text = _DEPLOY.read_text(encoding="utf-8")
    call = f'deploy_tree_swap --lock {declared.attr("lock")} "$repo_root/{declared.source}" {declared.destination}'
    assert call in text
    # The selection the probe table row sees is the deployer's default selection.
    assert _RUNTIME_ROW in (_REPO / "configs/runtime-package-manifest.txt").read_text().splitlines()
    assert declared.attr("profile") == "python" and not declared.attr("files")
    assert _shipped_set(), "the python profile must select files"


def test_the_deployer_swaps_the_runtime_under_the_watch_lock(tmp_path: Path) -> None:
    home, env = _rig(tmp_path)
    runtime = home / _DEST
    runtime.mkdir(parents=True)
    (runtime / "stale.py").write_text("stale\n", encoding="utf-8")
    lock = home / _LOCK
    lock.parent.mkdir(parents=True)
    lock.write_text("keep\n", encoding="utf-8")

    result = _deploy(env)

    assert result.returncode == 0, result.stderr
    assert runtime.is_dir() and not runtime.is_symlink()
    assert _snapshot(runtime) == _shipped_set()
    assert lock.read_text(encoding="utf-8") == "keep\n"
    wrapper = home / ".hermes/scripts/rag_ingest_watch.py"
    assert wrapper.read_bytes() == (_REPO / _SOURCE / "cron/rag_ingest_watch.py").read_bytes()
    backups = list((home / ".hermes/rag_ingest_runtime").glob("rag_ingest.old.*"))
    assert len(backups) == 1 and (backups[0] / "stale.py").is_file()
    assert not list((home / ".hermes/rag_ingest_runtime").glob("*.staging.*"))


def test_a_running_ingest_blocks_the_swap(tmp_path: Path) -> None:
    home, env = _rig(tmp_path)
    runtime = home / _DEST
    runtime.mkdir(parents=True)
    (runtime / "a.py").write_text("old\n", encoding="utf-8")
    before = _snapshot(runtime)
    lock = home / _LOCK
    lock.parent.mkdir(parents=True)
    for name in ("locked", "unlock"):
        os.mkfifo(tmp_path / name)
    ready_fd = os.open(tmp_path / "locked", os.O_RDWR)
    release_fd = os.open(tmp_path / "unlock", os.O_RDWR)
    holder = subprocess.Popen(
        ("bash", "-c", f'exec 9>>"{lock}"; flock 9; printf "ready\\n" > "{tmp_path}/locked"; '
         f'read -r _ < "{tmp_path}/unlock"'),
        env=env, start_new_session=True,
    )
    try:
        assert select.select([ready_fd], [], [], 10)[0], "lock holder never signalled"
        assert os.read(ready_fd, 6) == b"ready\n"
        result = _deploy(env)
        assert result.returncode == 6, result.stderr
        assert _snapshot(runtime) == before
        assert not list((home / ".hermes/rag_ingest_runtime").glob("rag_ingest.*"))
        assert not (home / ".hermes/scripts/rag_ingest_watch.py").exists()
    finally:
        os.write(release_fd, b"go\n")
        holder.wait(timeout=10)
        os.close(ready_fd)
        os.close(release_fd)


def test_the_recall_reader_is_accounted_for() -> None:
    declared = _declaration()
    assert declared.policy != "retired" and declared.kind == "tree"
    recall = (_REPO / "skills/recall/scripts/recall_cli.py").read_text(encoding="utf-8")
    match = re.search(r'_RUNTIME_DEFAULT\s*=\s*"~/(\.hermes/[^"]+)"', recall)
    assert match, "recall_cli no longer names a runtime default"
    assert str(Path(declared.destination).parent) == match.group(1)
    wrapper = (_REPO / _SOURCE / "cron/rag_ingest_watch.py").read_text(encoding="utf-8")
    assert "rag_ingest_runtime" in wrapper

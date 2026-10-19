"""todo 변경 CLI의 governed-copy 계약을 고정한다."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import skill_mount

SCRIPTS = Path(__file__).parents[2] / "skills" / "todo" / "scripts"
CLI = SCRIPTS / "todo_cli.py"


def _run(script: Path, live: Path, command: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, AUTOPHAGY_SKILL_LIVE_ROOT=str(live))
    return subprocess.run(
        [sys.executable, str(script), command, "--title", "x",
         "--origin-channel-id", "123456789012345678",
         "--origin-message-id", "123456789012345679"],
        capture_output=True, text=True, env=env, check=False,
    )


def test_mutation_is_blocked_outside_governed_copy_but_reads_are_not(tmp_path: Path) -> None:
    live = tmp_path / "live"
    governed = live / "todo" / "scripts"
    governed.mkdir(parents=True)
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, governed / source.name)

    blocked = _run(CLI, live, "create")
    assert blocked.returncode == 3
    assert skill_mount.STALE_COPY_MARKER in blocked.stderr

    read = _run(CLI, live, "plan")
    assert skill_mount.STALE_COPY_MARKER not in read.stderr

    allowed = _run(governed / "todo_cli.py", live, "create")
    assert skill_mount.STALE_COPY_MARKER not in allowed.stderr


def test_guard_constants_match_canonical() -> None:
    import sys as _sys
    _sys.path.insert(0, str(SCRIPTS))
    import todo_governed

    assert todo_governed.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert todo_governed.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert todo_governed.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER

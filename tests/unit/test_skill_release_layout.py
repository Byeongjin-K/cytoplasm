"""CLI entrypoints must run from the immutable release layout, not only a checkout.

The live store mounts a skill at ``/srv/autophagy-skills/releases/<name>/<sha256>/``,
where no importable ``skills`` package sits above the scripts directory. A naive
``sys.path.insert(parents[3])`` + ``from skills.<name>.scripts import …`` works in a
checkout and — by namespace-package accident — in the ``~/.hermes/skills/<name>``
sandbox staging, then crashes only on the mounted release (2026-08-22: topics and
prompt both died with ``ModuleNotFoundError: No module named 'skills'`` in
production). These tests stage each CLI in the release layout and execute it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _stage_release(tmp_path: Path, name: str) -> Path:
    release = tmp_path / "releases" / name / ("a" * 64)
    release.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "skills" / name, release)
    for cache in release.rglob("__pycache__"):
        shutil.rmtree(cache)
    return release


def _run(entrypoint: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(entrypoint), *args],
        env={"PATH": os.environ["PATH"], **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def test_prompt_cli_runs_from_the_release_layout(tmp_path: Path) -> None:
    release = _stage_release(tmp_path, "prompt")

    result = _run(release / "scripts" / "prompt_cli.py", "search", "demo", env={"HOME": str(tmp_path)})

    assert "No module named 'skills'" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr

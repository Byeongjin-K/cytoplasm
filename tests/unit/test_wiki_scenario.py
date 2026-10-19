from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import unit_node


_REPO = Path(__file__).resolve().parents[2]
_SCENARIO = _REPO / "skills" / "wiki" / "scripts" / "scenario.sh"


def test_wiki_scenario_runs_with_staged_interop_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: an isolated runtime and a parent credential excluded from the allowlist.
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "DUMMY-parent-only")
    runtime = tmp_path / "runtime"
    _ = runtime.mkdir()
    _ = shutil.copytree(_REPO / "automation", runtime / "automation")
    environment = {
        **unit_node.env(),
        "AUTOPHAGY_DEMO_SECRET": "DUMMY-wiki-scenario",
        "AUTOPHAGY_REPO_ROOT": str(runtime),
        "HOME": str(tmp_path),
        "INTEROP_RUNTIME": str(runtime),
        "PATH": os.environ["PATH"],
    }

    # When: the wiki scenario runs outside the source checkout.
    result = subprocess.run(
        ["bash", str(_SCENARIO)],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        env=environment,
        text=True,
    )

    # Then: the staged signed-confirm path completes fully offline.
    assert result.returncode == 0, result.stderr
    assert "SCENARIO-PASS leg=signed-confirm" in result.stdout

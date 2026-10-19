from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
import unit_node


_REPO = Path(__file__).resolve().parents[2]
_SCENARIO = _REPO / "skills" / "calendar" / "scripts" / "scenario.sh"


def test_calendar_scenario_runs_with_staged_interop_runtime(tmp_path: Path) -> None:
    # Given: an isolated runtime containing only the staged interop package.
    runtime = tmp_path / "runtime"
    _ = runtime.mkdir()
    _ = shutil.copytree(_REPO / "automation", runtime / "automation")
    environment = {
        **unit_node.env(),
        "AUTOPHAGY_DEMO_SECRET": "DUMMY-calendar-scenario",
        "AUTOPHAGY_REPO_ROOT": str(runtime),
        "HOME": str(tmp_path),
        "INTEROP_RUNTIME": str(runtime),
        "PATH": os.environ["PATH"],
    }

    # When: the deployed scenario is run without source-repository config access.
    result = subprocess.run(
        ["bash", str(_SCENARIO)],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        env=environment,
        text=True,
    )

    # Then: solo ambiguity and signed confirmation both complete offline.
    assert result.returncode == 0, result.stderr
    assert "SCENARIO-PASS leg=signed-confirm" in result.stdout

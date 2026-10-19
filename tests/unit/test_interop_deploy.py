"""Retired interop deployment refuses the old Hermes restart switch."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_restart_is_refused_before_any_remote_call(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "automation/interop/deploy.sh"
    result = subprocess.run(["bash", str(script), "--restart"], env={**os.environ, "HOME": str(tmp_path)},
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "no restart option" in result.stderr

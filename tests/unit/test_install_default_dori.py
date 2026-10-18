"""Public P6 entrypoints must not silently provision a legacy Hermes node."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation.install import entrypoint

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("command", (
    ("-m", "automation.install", "--dry-run"),
    ("-m", "automation.install", "--profile", "dori", "--dry-run"),
    ("-m", "automation.install.wizard", "--dry-run-only"),
))
def test_default_entrypoints_plan_only_dori_without_writing(
    tmp_path: Path, command: tuple[str, ...],
) -> None:
    # Given: no Hermes, credentials, Bun or systemd are required for a plan.
    home = tmp_path / "home"
    home.mkdir()
    # When
    result = subprocess.run(
        (sys.executable, *command), cwd=REPO,
        env={**os.environ, "HOME": str(home)}, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, check=False,
    )
    # Then: inspect machine plan kinds, not explanatory prose.
    assert result.returncode == 0, result.stdout + result.stderr
    kinds = {
        line.split()[1] for line in result.stdout.splitlines()
        if line.split() and line.split()[0].rstrip(".").isdigit()
    }
    assert kinds == {
        "dori-fork", "dori-skill-link", "dori-cli", "dori-config",
        "dori-role-lead", "dori-role-yoodori",
    }
    assert list(home.iterdir()) == []


def test_shell_quickstart_defaults_to_dori(tmp_path: Path) -> None:
    # Given
    home = tmp_path / "home"
    home.mkdir()
    # When
    result = subprocess.run(
        ("bash", "automation/install/quickstart.sh", "--dry-run-only"),
        cwd=REPO, env={**os.environ, "HOME": str(home)}, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, check=False,
    )
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DORI-PLAN-OK" in result.stdout
    assert "INSTALL PLAN" not in result.stdout
    assert list(home.iterdir()) == []


def test_legacy_arguments_without_profile_cannot_select_hermes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("HOME", str(tmp_path))
    # When / Then: no silent fallback based on an old flag.
    with pytest.raises(SystemExit) as error:
        entrypoint.main(("--update-trust-key", "/unused", "--dry-run"))
    assert error.value.code == 2
    assert list(tmp_path.iterdir()) == []

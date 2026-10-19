"""The rehearsal covers the pinned Dori fork's install and update path before a card.

Lead 2026-10-19: a pin bump ships a new fork commit to every install, so the card waits for that
exact commit to be fetched, verified and dry-run installed. New file so the recorded pin and
rehearsal cases keep their shape.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation import dori_pin
from tests.unit.test_release_rehearsal import HEAD, _rehearse


def _fork(tmp_path: Path) -> tuple[str, str]:
    repo = tmp_path / "fork"
    repo.mkdir()
    git = ("git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid")
    subprocess.run((*git, "init", "--quiet"), check=True)
    (repo / "README").write_text("fork")
    subprocess.run((*git, "add", "README"), check=True)
    subprocess.run((*git, "commit", "--quiet", "-m", "pin"), check=True)
    sha = subprocess.run((*git, "rev-parse", "HEAD"), check=True, capture_output=True, text=True).stdout.strip()
    return f"file://{repo}", sha


@pytest.mark.parametrize(("installer_rc", "pinned", "result"), [(0, True, 0), (3, True, 1), (0, False, 1)])
def test_rehearse_fetches_the_exact_commit_and_dry_runs_the_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installer_rc: int, pinned: bool, result: int,
) -> None:
    url, sha = _fork(tmp_path)
    pin = dori_pin.DoriPin(repo=url, commit=sha if pinned else "f" * 40)
    real = subprocess.run
    seen: list[tuple[str, ...]] = []

    def run(argv, *args, **kwargs):
        if "automation.install.dori" in argv:
            seen.append(tuple(argv))
            return subprocess.CompletedProcess(argv, installer_rc, "", "")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(dori_pin.subprocess, "run", run)
    assert dori_pin._rehearse(pin) == result
    if pinned:
        assert seen and "--dry-run" in seen[0] and seen[0][seen[0].index("--src") + 1].endswith("/fork")


def test_a_failing_pin_check_refuses_the_rehearsal(tmp_path: Path) -> None:
    result, _ = _rehearse(tmp_path, pin_check="false")
    assert result.returncode == 4
    assert result.stdout.splitlines()[-1] == "REHEARSAL-REFUSED step=dori-pin rc=4"
    assert HEAD

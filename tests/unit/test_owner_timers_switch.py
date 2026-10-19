"""Owner timer switch boundary; new file to leave FS3-pinned test outputs unchanged."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JOBS = ("budget-watch", "mail-attachment-drive-watch")


@pytest.mark.parametrize("raw", [
    b"", b"budget-watch", b"budget-watch\r\n", b" budget-watch\n",
    b"budget-watch\nbudget-watch\n", b"mail-attachment-drive-watch\nbudget-watch\n",
    b"budget-watch\n\n", b"Budget-watch\n", b"not-declared-watch\n", b"\xff\n",
])
def test_malformed_host_switch_refuses_without_normalizing(tmp_path: Path, raw: bytes) -> None:
    # Given
    flag = tmp_path / ".hermes/release-flags/owner-timers-off"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(raw)
    # When
    result = subprocess.run([sys.executable, "-B", "-m", "automation.owner_timers", "switch"],
                            cwd=ROOT, env={**os.environ, "HOME": str(tmp_path)},
                            capture_output=True, check=False)
    # Then
    assert result.returncode == 4, result.stderr
    assert result.stdout == b""


def test_absent_host_switch_is_empty_and_has_no_approval_surface(tmp_path: Path) -> None:
    # Given
    from automation import owner_timers
    # When
    names = owner_timers.release_switch(tmp_path)
    # Then
    assert names == ()
    assert owner_timers.surfaces(names) == ()


def test_plan_binds_exact_list_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    # Given
    from automation import owner_timers, release_approval
    from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args

    _fake_plan(monkeypatch)
    args = _plan_args()
    args.repo = str(ROOT)
    args.owner_timers_off = "/".join(JOBS)
    monkeypatch.setenv("RELEASE_NOTES_DIR", str(tmp_path / "notes"))
    # When
    result = release_approval.cmd_plan(args)
    # Then
    assert result == 0
    planned = json.loads(capsys.readouterr().out)
    assert all(list(row) in planned["surface_digests"] for row in owner_timers.surfaces(JOBS))
    assert owner_timers.surfaces(JOBS)[0][1] != owner_timers.surfaces(JOBS[:1])[0][1]

"""Exercise both real shell probes across a bounded release switch."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from automation import release_switch_grace
from automation.skill_review import skill_digest

_REPO = Path(__file__).resolve().parents[2]
_OLD = "a" * 40
_NEW = "b" * 40


@pytest.fixture
def switch(tmp_path: Path) -> Path:
    now = int(time.time())
    for sha, age in ((_OLD, 3600), (_NEW, 60)):
        release = tmp_path / "releases" / sha
        (release / "skills" / "mail").mkdir(parents=True)
        (release / ".origin-sha").write_text(sha + "\n", encoding="utf-8")
        (release / "skills" / "mail" / "SKILL.md").write_text(sha, encoding="utf-8")
        (release / "automation").mkdir()
        for name in ("skill_mount_drift.py", "skill_review.py"):
            shutil.copyfile(_REPO / "automation" / name, release / "automation" / name)
        os.utime(release, (now - age, now - age))
    current = tmp_path / "current"
    current.symlink_to(tmp_path / "releases" / _NEW)
    os.utime(current, (now - 60, now - 60), follow_symlinks=False)
    live = tmp_path / "live"
    live.mkdir()
    digest = skill_digest(tmp_path / "releases" / _OLD / "skills" / "mail")
    (live / "mail").symlink_to(tmp_path / "mounted" / digest)
    (tmp_path / "receipt.json").write_text(
        json.dumps({"release_sha": _OLD, "version": 2}), encoding="utf-8"
    )
    return tmp_path


def _probe(root: Path, probe: str) -> subprocess.CompletedProcess[str]:
    library, function, target = (
        ("release_receipt_probe.sh", "probe_release_fully_deployed", "receipt.json")
        if probe == "receipt"
        else ("skill_mount_probe.sh", "probe_skill_mounts_current", "live")
    )
    return subprocess.run(
        ["bash", "-c", f'source "{_REPO}/automation/{library}"; {function} node ops "$1"',
         "probe", str(root / target)],
        env={**os.environ, "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(root / "current"),
             "RUNTIME_RELEASE_CURRENT": str(root / "current"),
             "AUTOPHAGY_RUNTIME_ROOT": str(root / "current")},
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("probe", ["receipt", "mount"])
def test_fresh_previous_generation_warns(switch: Path, probe: str) -> None:
    result = _probe(switch, probe)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELEASE-SWITCH-GRACE-WARN" in result.stdout + result.stderr


def test_fresh_absent_receipt_warns(switch: Path) -> None:
    (switch / "receipt.json").unlink()
    result = _probe(switch, "receipt")
    assert result.returncode == 0, result.stderr
    assert "RELEASE-SWITCH-GRACE-WARN" in result.stderr


@pytest.mark.parametrize("probe", ["receipt", "mount"])
@pytest.mark.parametrize("age", [900, 2400])
def test_expired_switch_fails(switch: Path, probe: str, age: int) -> None:
    stamp = int(time.time()) - age
    os.utime(switch / "current", (stamp, stamp), follow_symlinks=False)
    result = _probe(switch, probe)
    assert result.returncode != 0
    assert "RELEASE-SWITCH-GRACE-WARN" not in result.stdout + result.stderr


@pytest.mark.parametrize("probe", ["receipt", "mount"])
def test_unreadable_age_fails(switch: Path, probe: str) -> None:
    # A readlink still works, but lstat cannot attest a symlink activation time.
    (switch / "current").unlink()
    (switch / "current").mkdir()
    result = _probe(switch, probe)
    assert result.returncode != 0


@pytest.mark.parametrize("probe", ["receipt", "mount"])
def test_unrelated_generation_fails(switch: Path, probe: str) -> None:
    (switch / "receipt.json").write_text(
        json.dumps({"release_sha": "c" * 40, "version": 2}), encoding="utf-8"
    )
    (switch / "live" / "mail").unlink()
    (switch / "live" / "mail").symlink_to(switch / "mounted" / ("c" * 64))
    result = _probe(switch, probe)
    assert result.returncode != 0


def test_unreadable_receipt_still_fails(switch: Path) -> None:
    (switch / "receipt.json").write_text("not-json", encoding="utf-8")
    result = _probe(switch, "receipt")
    assert result.returncode != 0
    assert "RECEIPT-UNKNOWN" in result.stderr


@pytest.mark.parametrize("probe", ["receipt", "mount"])
def test_reactivation_does_not_extend_grace(switch: Path, probe: str) -> None:
    stamp = int(time.time()) - 1800
    os.utime(switch / "releases" / _NEW, (stamp, stamp))
    result = _probe(switch, probe)
    assert result.returncode != 0


@pytest.mark.parametrize("probe", ["receipt", "mount"])
def test_future_activation_fails(switch: Path, probe: str) -> None:
    stamp = int(time.time()) + 3600
    os.utime(switch / "current", (stamp, stamp), follow_symlinks=False)
    result = _probe(switch, probe)
    assert result.returncode != 0


@pytest.mark.parametrize("surface", ["--receipt-sha", "--live-root"])
def test_age_read_error_refuses_grace(
    switch: Path, surface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable(_path: Path) -> os.stat_result:
        raise PermissionError("activation metadata unreadable")

    value = _OLD if surface == "--receipt-sha" else str(switch / "live")
    monkeypatch.setattr(Path, "lstat", unreadable)
    assert release_switch_grace.main(
        ["--current", str(switch / "current"), surface, value]
    ) == 1


@pytest.mark.parametrize("probe", ["receipt", "mount"])
def test_matching_current_generation_stays_pass(switch: Path, probe: str) -> None:
    (switch / "receipt.json").write_text(
        json.dumps({"release_sha": _NEW, "version": 2}), encoding="utf-8"
    )
    digest = skill_digest(switch / "releases" / _NEW / "skills" / "mail")
    (switch / "live" / "mail").unlink()
    (switch / "live" / "mail").symlink_to(switch / "mounted" / digest)
    result = _probe(switch, probe)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELEASE-SWITCH-GRACE-WARN" not in result.stdout + result.stderr


@pytest.mark.parametrize("damage", ["missing", "orphaned", "old-receipt", "broken-marker"])
def test_other_damage_is_not_a_release_switch(switch: Path, damage: str) -> None:
    if damage == "missing":
        (switch / "live" / "mail").unlink()
    elif damage == "orphaned":
        (switch / "live" / "extra").symlink_to(switch / "mounted" / ("d" * 64))
    elif damage == "old-receipt":
        (switch / "receipt.json").write_text(
            json.dumps({"release_sha": _OLD, "version": 1}), encoding="utf-8"
        )
    else:
        (switch / "releases" / _OLD / ".origin-sha").unlink()
    result = _probe(switch, "receipt" if damage == "old-receipt" else "mount")
    assert result.returncode != 0

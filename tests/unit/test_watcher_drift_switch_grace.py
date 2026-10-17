"""The watcher-wrapper probe waits out a release switch the same way receipts and mounts do.

Two releases in a row (10-16 v1.39.0, 10-17 v1.46.0) failed this check for exactly one sweep:
the release pointer had moved, but `deploy_all` had not yet copied the wrappers or refreshed the
SSH allowlist for a new wrapper row. Inside the bounded switch grace a row may be the previous
release's copy, or a row new in this release that is not readable or deployed yet. Anything
else still fails. Kept apart from `test_watcher_drift_probe.py` so the existing judgement
tests stay byte-identical.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_PROBE: Final = _REPO / "automation" / "watcher_drift_probe.sh"
_OLD: Final = "a" * 40
_NEW: Final = "b" * 40
_REJECT: Final = "REJECT"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def switch(tmp_path: Path) -> Path:
    now = int(time.time())
    for sha, age in ((_OLD, 3600), (_NEW, 60)):
        release = tmp_path / "releases" / sha
        (release / "w").mkdir(parents=True)
        (release / ".origin-sha").write_text(sha + "\n", encoding="utf-8")
        (release / "w" / "kept.py").write_text(f"kept {sha}", encoding="utf-8")
        if sha == _NEW:
            (release / "w" / "added.py").write_text("added", encoding="utf-8")
        os.utime(release, (now - age, now - age))
    current = tmp_path / "current"
    current.symlink_to(tmp_path / "releases" / _NEW)
    os.utime(current, (now - 60, now - 60), follow_symlinks=False)
    (tmp_path / "manifest.txt").write_text(
        "agent|w/kept.py|.hermes/scripts/kept.py|required\n"
        "agent|w/added.py|.hermes/scripts/added.py|required\n",
        encoding="utf-8",
    )
    return tmp_path


def _probe(root: Path, deployed: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """`deployed` maps a destination name to its sha, "" (absent) or REJECT (not allowlisted)."""
    lines = ["capture_on_node() {", '  local command="$2"']
    for name, answer in deployed.items():
        reply = "return 1" if answer == _REJECT else f"printf %s '{answer}'; return 0"
        lines.append(f'  case "$command" in *{name}*) {reply} ;; esac')
    lines.extend(["  return 0", "}"])
    stub = root / "stub.sh"
    stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return subprocess.run(
        ["bash", "-c",
         f'source "{stub}"; source "{_PROBE}"; '
         f'probe_watcher_wrappers_current node ops "{root / "manifest.txt"}"'],
        env={**os.environ, "HOME": str(root),
             "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(root / "current")},
        capture_output=True, text=True, check=False,
    )


def _current() -> dict[str, str]:
    return {"kept.py": _sha(f"kept {_NEW}"), "added.py": _sha("added")}


def test_fully_converged_release_passes_without_grace(switch: Path) -> None:
    result = _probe(switch, _current())
    assert result.returncode == 0, result.stderr
    assert "WATCHER-DRIFT-PASS: 2 wrapper(s)" in result.stderr
    assert "GRACE" not in result.stderr


def test_previous_release_copy_warns_inside_grace(switch: Path) -> None:
    """10-16 shape: the wrapper was still the previous release's bytes."""
    result = _probe(switch, {**_current(), "kept.py": _sha(f"kept {_OLD}")})
    assert result.returncode == 0, result.stderr
    assert "RELEASE-SWITCH-GRACE-WARN" in result.stderr
    assert "WATCHER-DRIFT-GRACE-PASS: 1 wrapper(s)" in result.stderr


@pytest.mark.parametrize("answer", [_REJECT, ""])
def test_row_new_in_this_release_warns_inside_grace(switch: Path, answer: str) -> None:
    """10-17 shape: the new row's read was not allowlisted yet (or the file was not copied)."""
    result = _probe(switch, {**_current(), "added.py": answer})
    assert result.returncode == 0, result.stderr
    assert "WATCHER-DRIFT-GRACE: agent:.hermes/scripts/added.py is new" in result.stderr


@pytest.mark.parametrize("answer", ["0" * 64, _REJECT, ""])
def test_a_row_the_previous_release_had_still_fails(switch: Path, answer: str) -> None:
    """Different from both generations, or a known row that cannot be read or is gone."""
    result = _probe(switch, {**_current(), "kept.py": answer})
    assert result.returncode != 0
    assert "WATCHER-DRIFT-GRACE-PASS" not in result.stderr


def test_unrelated_bytes_in_a_new_row_still_fail(switch: Path) -> None:
    result = _probe(switch, {**_current(), "added.py": "0" * 64})
    assert result.returncode != 0


def test_one_real_failure_is_not_hidden_by_a_converging_row(switch: Path) -> None:
    result = _probe(switch, {"kept.py": "0" * 64, "added.py": _REJECT})
    assert result.returncode != 0
    assert "differs from the release" in result.stderr


@pytest.mark.parametrize("age", [900, 2400])
def test_expired_switch_fails(switch: Path, age: int) -> None:
    stamp = int(time.time()) - age
    os.utime(switch / "current", (stamp, stamp), follow_symlinks=False)
    result = _probe(switch, {**_current(), "added.py": _REJECT})
    assert result.returncode != 0
    assert "WATCHER-DRIFT-UNKNOWN: cannot read agent:.hermes/scripts/added.py" in result.stderr


def test_no_previous_generation_means_no_grace(switch: Path) -> None:
    (switch / "releases" / _OLD / ".origin-sha").unlink()
    result = _probe(switch, {**_current(), "kept.py": _sha(f"kept {_OLD}")})
    assert result.returncode != 0

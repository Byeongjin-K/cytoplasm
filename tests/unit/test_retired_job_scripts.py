"""Retired script declarations and required job cleanup cover the same leftovers."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation import retired_jobs
from automation.deploy_declarations import all_declarations

ROOT = Path(__file__).resolve().parents[2]
CASES = (
    ("memory-curator-watch", "memory_curator_watch.py"),
    ("memory-relocate-watch", "memory_relocate_watch.py"),
    ("reminder-poller", "poll_reminders.py"),
)


def _stopped(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        argv, 3, "inactive" if argv[2] == "is-active" else "disabled", "",
    )


@pytest.mark.parametrize(("job", "script"), CASES)
def test_retired_wrapper_is_removed_when_its_timer_is_already_absent(
    tmp_path: Path, job: str, script: str,
) -> None:
    # Given: only a retired wrapper remains, beside an unrelated live script.
    path = tmp_path / ".hermes/scripts" / script
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old wrapper")
    live = path.with_name("unrelated.py")
    live.write_bytes(b"keep")
    assert retired_jobs.check(tmp_path, job, _stopped) == "stale"

    # When: the required derived job declaration converges.
    retired_jobs.install(tmp_path, job, _stopped)

    # Then: only the retired wrapper is removed and the derived observation is clean.
    assert not path.exists()
    assert live.read_bytes() == b"keep"
    assert retired_jobs.check(tmp_path, job, _stopped) == "ok"


def test_research_trends_retirement_keeps_its_script(tmp_path: Path) -> None:
    # Given: the research-trends timer is retired but its script is still a required file.
    script = tmp_path / ".hermes/scripts/research_trends_watch.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"kept")

    # When: its required derived job declaration is observed and converged.
    assert retired_jobs.check(tmp_path, "research-trends", _stopped) == "ok"
    retired_jobs.install(tmp_path, "research-trends", _stopped)

    # Then: no wrapper lookup fails and the script stays.
    assert script.read_bytes() == b"kept"
    assert retired_jobs.check(tmp_path, "research-trends", _stopped) == "ok"


@pytest.mark.parametrize(("job", "script"), CASES)
def test_retired_wrapper_is_declared_for_inventory(job: str, script: str) -> None:
    # Given: the same inventory the deploy verdict reads.
    rows = all_declarations(ROOT)
    destination = f".hermes/scripts/{script}"

    # When
    matches = [row for row in rows if row.account == "agent" and row.destination == destination]

    # Then: the wrapper is known but never installed as a live script again.
    assert len(matches) == 1
    assert matches[0].kind == "file"
    assert matches[0].policy == "retired"

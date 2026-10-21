"""Owner-off runtime regression; separate from FS3-pinned timer tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from automation import cron_timer, deploy_all, deploy_all_observe
from automation.deploy_declarations import parse_declaration_file
from tests.unit.test_cron_timer import JOB, TIMER, _calls
from tests.unit.test_cron_timer import node as node

ROOT = Path(__file__).resolve().parents[2]


def _mark(home: Path) -> Path:
    marker = home / ".hermes/owner-timers/off"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"doctor-watch\n")
    marker.chmod(0o600)
    return marker


def test_converge_disables_owner_off_job_instead_of_enabling(node: cron_timer.Node) -> None:
    # Given: actual command adapter over isolated fake systemctl and Hermes.
    _mark(node.home)
    node.unit("enable", "--now", TIMER)
    # When
    result = cron_timer.converge(node, JOB, root=str(ROOT))
    # Then
    assert "released-off" in result
    calls = _calls(node)
    assert f"systemctl disable --now {TIMER}" in calls
    assert calls.count(f"systemctl enable --now {TIMER}") == 1  # setup only
    assert node.unit("is-enabled", TIMER)[1].strip() == "disabled"
    assert node.unit("is-active", TIMER)[1].strip() == "inactive"


@pytest.mark.parametrize("enabled,expected", [(False, "ok"), (True, "stale")])
def test_check_reads_owner_off_timer_state(node: cron_timer.Node, enabled: bool, expected: str) -> None:
    # Given
    _mark(node.home)
    if enabled:
        node.unit("enable", "--now", TIMER)
    # When
    state, detail = cron_timer.check(node, JOB, root=str(ROOT))
    # Then
    assert (state, detail) == (expected, "released-off" if expected == "ok" else "released-off-drift")


def test_install_removes_listing_and_reenables_previous_timer(node: cron_timer.Node) -> None:
    # Given
    from automation import owner_timers

    owner_timers.install(node.home, ("doctor-watch",))
    assert (node.home / owner_timers.MARKER).stat().st_mode & 0o777 == 0o600
    # When
    owner_timers.install(node.home, ())
    # Then
    assert not (node.home / owner_timers.MARKER).exists()
    assert node.unit("is-enabled", TIMER)[1].strip() == "enabled"
    assert node.unit("is-active", TIMER)[1].strip() == "active"


def test_absent_listing_keeps_normal_convergence(node: cron_timer.Node) -> None:
    # Given
    from automation import owner_timers

    assert owner_timers.read_list(node.home / owner_timers.MARKER) == ()
    # When
    cron_timer.converge(node, JOB, root=str(ROOT))
    # Then
    assert node.unit("is-enabled", TIMER)[1].strip() == "enabled"
    assert node.unit("is-active", TIMER)[1].strip() == "active"


def test_planner_does_not_repeat_deployer_for_correctly_disabled_job(node: cron_timer.Node) -> None:
    # Given
    _mark(node.home)
    decl, = parse_declaration_file(
        "automation/doctor/deploy-manifest.txt",
        "agent|automation/doctor/x.py|doctor-watch|v2:cron;runner=systemd;"
        "schedule=17 * * * *;script=doctor_watch.py;deliver=local;mode=no-agent\n",
    )
    state, detail = cron_timer.check(node, JOB, root=str(ROOT))

    def runner(_account: str, _args: tuple[str, ...]) -> tuple[int, str]:
        return 0, f"TIMER-CHECK state={state} detail={detail} want=x\n"

    seen = deploy_all_observe._timer(decl, ROOT, runner)
    # When
    plan = deploy_all.parse_observations([
        f"OBS|release|{'a' * 40}", "OBS|mounts|judged",
        "OBS|home|agent|.hermes/scripts/x.py|automation/doctor/x.py|required|aaa|aaa",
        f"OBS|artifact|cron|agent|doctor-watch|automation/doctor|required|none|{seen.state}|{seen.want}|{seen.have}",
        "OBS|artifacts|1", "OBS|end",
    ])
    # Then
    assert "ACT|run-deployer|" not in deploy_all.render_actions(plan)
    assert seen.state == "ok"


@pytest.mark.parametrize("enabled,expected", [(False, "released-off"), (True, "unknown")])
def test_doctor_marks_only_disabled_inactive_owner_jobs_as_released_off(
    node: cron_timer.Node, enabled: bool, expected: str,
) -> None:
    # Given
    from automation.doctor.facts import _timers

    _mark(node.home)
    if enabled:
        node.unit("enable", "--now", TIMER)

    def run(argv: tuple[str, ...], _env: dict[str, str]) -> tuple[int, str]:
        return node.unit(*argv[2:])
    # When
    facts = _timers(ROOT, "agent", node.home, run, os.getuid())
    # Then
    assert next(f.timer for f in facts if f.name == "doctor-watch") == expected


def test_read_only_marker_check_detects_list_and_unit_drift(node: cron_timer.Node) -> None:
    # Given
    from automation import owner_timers

    marker = _mark(node.home)
    before = marker.read_bytes(), marker.stat().st_mtime_ns
    # When
    state = owner_timers.check(node.home, ("doctor-watch",))
    # Then
    assert state == "ok"
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before


def test_host_list_projects_off_before_marker_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the node has not installed its marker yet; the derived declaration owns the drift.
    declarations = parse_declaration_file(
        "automation/owner_timers/deploy-manifest.txt",
        "agent|automation/owner_timers|.hermes/owner-timers/off|v2:derived;algorithm=owner-timers-v1\n",
    ) + parse_declaration_file(
        "automation/xcal/deploy-manifest.txt",
        "agent|automation/xcal/x.py|calendar-confirm-watch|v2:cron;runner=systemd;"
        "schedule=17 * * * *;script=x.py;deliver=local;mode=no-agent\n",
    )
    monkeypatch.setattr(deploy_all_observe, "all_declarations", lambda _root: declarations)
    calls = []

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        calls.append((account, args))
        return (0, "stale") if args[0] == "owner-timers" else (64, "")
    # When
    lines = deploy_all_observe.collect(tmp_path, runner, "ops", owner_timers_off="calendar-confirm-watch")
    # Then
    assert not any(args[0] == "timer" for _account, args in calls)
    assert any(line.startswith("OBS|artifact|derived|agent|.hermes/owner-timers/off|"
                               "automation/owner_timers|required|none|stale|") for line in lines)
    assert not any("|cron|agent|calendar-confirm-watch|" in line for line in lines)

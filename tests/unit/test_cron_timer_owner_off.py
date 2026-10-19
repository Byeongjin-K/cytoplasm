"""Owner-off regressions live separately because FS3 freezes existing test files."""
import json

import pytest

from automation import cron_timer


@pytest.mark.parametrize("name", ["budget-watch", "mail-attachment-drive-watch"])
def test_converge_preserves_disabled_timer_when_owner_marker_exists(tmp_path, monkeypatch, name):
    # Given: an approved node marker and a disabled timer, without Hermes installed.
    marker = tmp_path / ".hermes/cron-timers/owner-off.mode"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(f"{name}\n".encode())
    node = cron_timer.Node(tmp_path)
    calls = []

    def unit(*args):
        calls.append(args)
        return 0, "disabled" if args[0] == "is-enabled" else "inactive"

    monkeypatch.setattr(node, "unit", unit)
    job = cron_timer.Job(name, "every 30m", "watch.py")
    # When: a release deployer converges this job.
    result = cron_timer.converge(node, job, root="/srv/current")
    # Then: nothing can re-enable it or require a Hermes listing.
    assert result == f"CRON-TIMER-UNCHANGED name={name} released-off"
    assert {args[0] for args in calls} == {"is-enabled", "is-active"}


@pytest.mark.parametrize("enabled,active,want", [
    ("disabled", "inactive", ("ok", "released-off")),
    ("enabled", "inactive", ("stale", "released-off-drift")),
    ("disabled", "active", ("stale", "released-off-drift")),
])
def test_check_reports_actual_timer_state_when_owner_marker_exists(
    tmp_path, monkeypatch, enabled, active, want,
):
    # Given: the node marker selects this job.
    marker = tmp_path / ".hermes/cron-timers/owner-off.mode"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"budget-watch\n")
    node = cron_timer.Node(tmp_path)
    monkeypatch.setattr(node, "unit", lambda *args: (
        0, enabled if args[0] == "is-enabled" else active,
    ))
    # When: the deployment probe checks the declared timer.
    result = cron_timer.check(
        node, cron_timer.Job("budget-watch", "every 30m", "watch.py"), root="/srv/current",
    )
    # Then: the release expects verified shutdown, not an enabled timer.
    assert result == want


def test_marker_removal_restores_normal_convergence_when_flag_is_deleted(tmp_path, monkeypatch):
    # Given: real command adapters with isolated Hermes and systemctl executables.
    from automation import cron_timer_owner_off as owner
    from tests.unit.test_cron_timer import _exe, _HERMES, _SYSTEMCTL

    scripts = tmp_path / ".hermes/scripts"
    scripts.mkdir(parents=True)
    (scripts / "watch.py").write_text("pass\n")
    (tmp_path / "jobs.json").write_text(json.dumps([
        {"id": "ab12", "name": "budget-watch", "state": "paused"},
    ]))
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "jobs.json"))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls"))
    monkeypatch.setenv("FAKE_UNITS", str(tmp_path / "units.json"))
    monkeypatch.setenv("CRON_TIMER_HERMES", str(_exe(tmp_path / "hermes", _HERMES)))
    monkeypatch.setenv("CRON_TIMER_SYSTEMCTL", str(_exe(tmp_path / "systemctl", _SYSTEMCTL)))
    node = cron_timer.Node(tmp_path)
    job = cron_timer.Job("budget-watch", "every 30m", "watch.py")
    assert owner.install(node, ("budget-watch",)) == 0
    # When: a later release removes the owner-off selection and converges normally.
    assert owner.install(node, ()) == 0
    cron_timer.converge(node, job, root="/srv/current")
    # Then: ordinary Hermes safety checks restore an enabled and active timer.
    assert cron_timer.check(node, job, root="/srv/current") == ("ok", "-")


def test_deploy_observation_accepts_released_off_without_pending_timer_deployer(tmp_path):
    # Given: the real declaration and the check-declared protocol's released-off result.
    from automation import deploy_all_observe
    from automation.deploy_declarations import all_declarations

    row = next(d for d in all_declarations(owner_repo())
               if d.kind == "cron" and d.destination == "budget-watch")
    # When: the observer receives a verified off state.
    result = deploy_all_observe._timer(row, tmp_path, lambda *_: (
        0, "TIMER-CHECK state=ok detail=released-off want=unused\n",
    ))
    # Then: this job contributes no pending deployment drift.
    assert result.state == "ok"


def owner_repo():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("enabled,active,want", [
    ("disabled", "inactive", "released-off"),
    ("enabled", "inactive", "released-off-drift"),
    ("disabled", "active", "released-off-drift"),
])
def test_doctor_reads_shutdown_when_owner_selected_timer_exists(tmp_path, enabled, active, want):
    # Given: a deployed owner selection.
    from automation.cron_timer_owner_off import MARKER
    from automation.doctor.facts import _timers

    marker = tmp_path / MARKER
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"budget-watch\n")
    # When: doctor reads the actual systemctl states.
    timers = _timers(owner_repo(), "agent", tmp_path, lambda argv, _env: (
        0, enabled if "is-enabled" in argv else active,
    ), 1000)
    # Then: released-off is shown only for disabled and inactive timers.
    assert next(timer for timer in timers if timer.name == "budget-watch").timer == want

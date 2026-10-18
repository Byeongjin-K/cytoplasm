"""Release stop switches preserve units and data and are reversible."""
from pathlib import Path

import pytest

from automation import legacy_services as services


@pytest.mark.parametrize("name", ["report-hub", "hermes-dashboard"])
def test_switch_requires_exact_off(tmp_path: Path, name: str):
    assert services.release_mode(tmp_path, name) == "on"
    flag = tmp_path / ".hermes/release-flags" / f"{name}-off"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"off")
    assert services.release_mode(tmp_path, name) == "off"
    flag.write_bytes(b"off\n")
    with pytest.raises(ValueError):
        services.release_mode(tmp_path, name)


@pytest.mark.parametrize("name", ["report-hub", "hermes-dashboard"])
def test_stop_and_restore_verify_units_without_deleting_data(tmp_path, name):
    states = {unit: "active" for unit in services.UNITS[name]}
    calls = []
    database = tmp_path / ".hermes/kanban.db"
    database.parent.mkdir()
    database.write_bytes(b"board-data")

    def run(argv):
        calls.append(argv)
        verb, unit = argv[-2:]
        if verb in {"stop", "start"}:
            states[unit] = "inactive" if verb == "stop" else "active"
            return 0, ""
        return (0 if states[unit] == "active" else 3), states[unit]

    assert services.install(tmp_path, name, "off", run) == 0
    assert services.released_off(tmp_path, name)
    assert services.check(tmp_path, name, "off", run) == "ok"
    assert services.check(tmp_path, name, "on", run) == "stale"
    assert services.install(tmp_path, name, "on", run) == 0
    assert not services.released_off(tmp_path, name)
    assert all(state == "active" for state in states.values())
    assert database.read_bytes() == b"board-data"
    assert not any("disable" in call or "remove" in call for call in calls)


def test_failed_stop_does_not_claim_expected_shutdown(tmp_path):
    assert services.install(tmp_path, "report-hub", "off", lambda _argv: (1, "")) != 0
    assert not services.released_off(tmp_path, "report-hub")


def test_off_marker_does_not_hide_running_unit(tmp_path):
    marker = services.marker_path(tmp_path, "report-hub")
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"off")
    assert services.check(tmp_path, "report-hub", "off", lambda _argv: (0, "active")) == "stale"


def test_cli_stops_checks_and_restores_with_local_systemctl_fake(tmp_path):
    import os
    import subprocess
    import sys

    binary = tmp_path / "bin"
    binary.mkdir()
    systemctl = binary / "systemctl"
    systemctl.write_text(
        '#!/bin/bash\ncase "$2" in\n'
        'stop) printf inactive > "$STATE";;\n'
        'start) printf active > "$STATE";;\n'
        'is-active) value="$(< "$STATE")"; printf "%s\\n" "$value"; [[ "$value" == active ]] || exit 3;;\n'
        '*) exit 99;; esac\n'
    )
    systemctl.chmod(0o755)
    state = tmp_path / "state"
    state.write_text("active")
    environment = {**os.environ, "HOME": str(tmp_path), "STATE": str(state),
                   "PATH": f"{binary}:{os.environ['PATH']}"}
    for command, mode, output in (("install", "off", ""), ("check", "off", "ok\n"),
                                  ("install", "on", ""), ("check", "on", "ok\n")):
        done = subprocess.run(
            (sys.executable, "-m", "automation.legacy_services", command,
             "--name", "report-hub", "--mode", mode),
            env=environment, capture_output=True, text=True, check=False, timeout=10,
        )
        assert done.returncode == 0, done.stderr
        assert done.stdout == output
    assert state.read_text() == "active"

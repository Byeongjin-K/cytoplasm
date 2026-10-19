"""A never-installed legacy view unit counts as already off.

Separate file because the tests/AGENTS.md FS3 rule pins the existing legacy_services test
files' output hashes. "Missing" means systemd's own LoadState=not-found; a probe that fails
any other way must still go through stop and read-back
(test_legacy_services.py::test_failed_stop_does_not_claim_expected_shutdown).
"""
from pathlib import Path

from automation import legacy_services as services

SHOW = ("show", "-p", "LoadState", "--value")


def _is_show(argv) -> bool:
    return tuple(argv[2:6]) == SHOW


def test_off_succeeds_when_every_report_unit_is_missing(tmp_path: Path):
    """Given missing units, off mode records the already-off state."""
    calls = []

    def run(argv):
        calls.append(argv)
        if _is_show(argv):
            return 0, "not-found"
        if argv[2] == "stop":
            return 5, ""
        if argv[2] == "is-active":
            return 3, "inactive"
        raise AssertionError(argv)

    assert services.install(tmp_path, "report-hub", "off", run) == 0
    assert services.marker_path(tmp_path, "report-hub").read_bytes() == b"off"
    assert [call[6] for call in calls if _is_show(call)] == list(services.UNITS["report-hub"])
    assert not [call for call in calls if call[2] == "stop"]


def test_off_stops_installed_unit_and_skips_missing_unit(tmp_path: Path):
    """Given one present unit, only it is stopped and verified."""
    installed = services.UNITS["report-hub"][0]
    missing = services.UNITS["report-hub"][1]
    calls = []

    def run(argv):
        calls.append(argv)
        if _is_show(argv):
            return 0, "loaded" if argv[6] == installed else "not-found"
        if argv[2] == "stop":
            return 0, ""
        if argv[2] == "is-active":
            return 3, "inactive"
        raise AssertionError(argv)

    assert services.install(tmp_path, "report-hub", "off", run) == 0
    assert ("systemctl", "--user", "stop", installed) in calls
    assert ("systemctl", "--user", "stop", missing) not in calls
    assert ("systemctl", "--user", "is-active", installed) in calls


def test_stop_failure_names_existing_unit(tmp_path: Path, capsys):
    """Given an installed unit whose stop fails, report the unit and claim nothing."""
    unit = services.UNITS["report-hub"][0]

    def run(argv):
        if _is_show(argv):
            return 0, "loaded"
        if argv[2] == "stop":
            return 5, ""
        raise AssertionError(argv)

    assert services.install(tmp_path, "report-hub", "off", run) == 5
    assert f"unit={unit}" in capsys.readouterr().err
    assert not services.released_off(tmp_path, "report-hub")


def test_failing_probe_is_not_read_as_missing(tmp_path: Path):
    """Given a probe that fails (not 'not-found'), the stop still runs and its failure counts."""
    calls = []

    def run(argv):
        calls.append(argv)
        return 1, ""

    assert services.install(tmp_path, "report-hub", "off", run) == 5
    assert ("systemctl", "--user", "stop", services.UNITS["report-hub"][0]) in calls
    assert not services.released_off(tmp_path, "report-hub")


def test_check_accepts_missing_units_after_off_install(tmp_path: Path):
    """Given all report units are missing and marked off, check passes."""

    def run(argv):
        return 3, "inactive"

    def install_run(argv):
        if _is_show(argv):
            return 0, "not-found"
        return run(argv)

    assert services.install(tmp_path, "report-hub", "off", install_run) == 0
    assert services.check(tmp_path, "report-hub", "off", run) == "ok"

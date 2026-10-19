"""Switching off a legacy service that was never installed completes instead of failing silently.

PR #104 gap 7 (2026-10-19): on a node without report-hub, `report-hub-off` made the deployer exit 5
with no message, because stopping a missing unit fails. New file so the recorded cases in
test_legacy_services.py keep their shape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from automation import legacy_services as services


def _systemctl(installed: set[str]):
    def run(argv: tuple[str, ...]) -> tuple[int, str]:
        verb, unit = argv[2], argv[3]
        if verb == "show":
            return 0, "loaded" if unit in installed else "not-found"
        if verb == "stop":
            return (0, "") if unit in installed else (5, "")
        return 3, "inactive"
    return run


def test_a_never_installed_service_is_marked_off(tmp_path: Path) -> None:
    run = _systemctl(set())
    assert services.install(tmp_path, "report-hub", "off", run) == 0
    assert services.released_off(tmp_path, "report-hub")
    assert services.check(tmp_path, "report-hub", "off", run) == "ok"


def test_a_loaded_unit_that_will_not_stop_still_fails_and_names_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    unit = services.UNITS["report-hub"][0]

    def run(argv: tuple[str, ...]) -> tuple[int, str]:
        return (0, "loaded") if argv[2] == "show" else (1, "")

    assert services.install(tmp_path, "report-hub", "off", run) == 5
    assert not services.released_off(tmp_path, "report-hub")
    assert f"LEGACY-SERVICE-FAIL unit={unit} step=stop" in capsys.readouterr().err

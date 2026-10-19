"""Doctor gate mode uses the real mint boundary, without executing legacy gws."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from automation.doctor.capabilities import evaluate
from automation.doctor.facts import gather
from automation.doctor.google import GwsStatus
from automation.google_gate.mode import marker
from automation.install.checks import Status
from google_gate_helpers import gate, payload


def mode(home: Path, port: int) -> None:
    target = marker(home)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"port": port, "installed_at": "test", "sha256": "0" * 64}))


def observe(home: Path, online: bool = True):
    def run(argv, extra):
        assert argv[1:] != ("auth", "status"), "legacy credentials must not be accessed"
        return 0, "inactive\n"
    return gather(account="agent", role="agent", home=home, gateway_unit="unused",
                  online=online, run=run, repo=home, uid=2012)


def test_doctor_when_gate_mints_passes_without_legacy_files(tmp_path: Path) -> None:
    # Given
    with gate(payload()) as port:
        mode(tmp_path, port)
        # When
        facts = observe(tmp_path)
    # Then
    assert facts.gws == GwsStatus(True, ("scope-a", "scope-b"))
    assert "access_token" not in repr(facts)
    assert next(f for f in evaluate(facts) if f.key == "google-workspace").status is Status.PASS


@pytest.mark.parametrize("http_status", [403, 503])
def test_doctor_when_gate_refuses_fails_instead_of_legacy_login(tmp_path: Path, http_status: int) -> None:
    # Given
    with gate(b"{}", http_status) as port:
        mode(tmp_path, port)
        # When
        facts = observe(tmp_path)
    # Then
    finding = next(f for f in evaluate(facts) if f.key == "google-workspace")
    assert finding.status is Status.FAIL
    assert facts.gws == f"http-{http_status}"
    assert finding.steps


def test_doctor_when_gate_down_fails(tmp_path: Path) -> None:
    # Given
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        mode(tmp_path, reserved.getsockname()[1])
        # When
        facts = observe(tmp_path)
    # Then
    assert facts.gws == "unreachable"
    assert next(f for f in evaluate(facts) if f.key == "google-workspace").status is Status.FAIL


def test_doctor_when_offline_does_not_contact_gate(tmp_path: Path) -> None:
    # Given
    mode(tmp_path, 1)
    # When
    facts = observe(tmp_path, online=False)
    # Then
    assert facts.google_gate and facts.gws is None


def test_doctor_when_marker_corrupt_fails_closed(tmp_path: Path) -> None:
    # Given
    mode(tmp_path, 1)
    marker(tmp_path).write_text("{}")
    # When
    facts = observe(tmp_path)
    # Then
    assert facts.gws == "marker-invalid"
    assert next(f for f in evaluate(facts) if f.key == "google-workspace").status is Status.FAIL

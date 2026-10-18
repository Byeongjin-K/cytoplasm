"""A release marker makes a stopped gateway expected, never an unmarked outage."""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from automation import gateway_generation
from automation.doctor.capabilities import evaluate
from automation.doctor.facts import gather
from automation.install.checks import Status
from tests.unit.test_doctor import _connected_home, _run

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("marker,status", [(b"stopped", Status.PASS), (None, Status.FAIL),
                                         (b"stopped\n", Status.FAIL)])
def test_doctor_accepts_only_release_marked_inactive_gateway(
    tmp_path: Path, marker: bytes | None, status: Status,
) -> None:
    # Given
    home = _connected_home(tmp_path)
    if marker is not None:
        (home / ".hermes/gateway-stopped-by-release").write_bytes(marker)
    # When
    facts = gather(account="agent", role="agent", home=home, gateway_unit="hermes-gateway.service",
                   online=False, run=_run(gateway="inactive"), uid=1000)
    line = next(f for f in evaluate(facts) if f.key == "hermes-gateway")
    # Then
    assert line.status is status
    assert not line.steps if status is Status.PASS else line.steps


@pytest.mark.parametrize("marker,code", [(b"stopped", 0), (None, 1), (b"stopped\n", 1)])
def test_healthcheck_accepts_only_release_marked_inactive_gateway(
    tmp_path: Path, marker: bytes | None, code: int,
) -> None:
    # Given: execute the actual fixed remote command in a fake service account HOME.
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    if marker is not None:
        (home / ".hermes/gateway-stopped-by-release").write_bytes(marker)
    script = f"""
source {shlex.quote(str(REPO / 'automation/healthcheck_probes.sh'))}
valid_account() {{ return 0; }}
valid_unit() {{ return 0; }}
log() {{ printf '%s\\n' "$*"; }}
capture_on_node() {{
  local command="$2"
  command="${{command#sudo -n -u agent -H }}"
  systemctl() {{ printf inactive; return 3; }}
  id() {{ printf 1000; }}
  export -f systemctl id
  eval "$command"
}}
probe_user_unit_active node agent hermes-gateway.service
"""
    # When
    result = subprocess.run(["bash", "-c", script], env={**os.environ, "HOME": str(home)},
                            capture_output=True, text=True, timeout=10, check=False)
    # Then
    assert result.returncode == code, result.stdout + result.stderr


def test_marked_generation_check_does_not_require_a_live_pid(tmp_path: Path) -> None:
    # Given
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes/gateway-stopped-by-release").write_bytes(b"stopped")
    # When
    state = gateway_generation.check(tmp_path, REPO, ())
    # Then
    assert state == ("ok", REPO.name, REPO.name, "stopped-by-release")

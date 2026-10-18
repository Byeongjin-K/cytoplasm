"""SIGTERM exit-code failure is stopped only with the release-owned marker."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.doctor.capabilities import _gateway
from automation.install.checks import Status

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("marked", [True, False])
def test_doctor_failed_state_requires_release_marker(marked):
    facts = SimpleNamespace(gateway="failed", gateway_stopped_by_release=marked, hermes_retired=False,
                            gateway_unit="hermes-gateway.service")
    assert _gateway(facts).status is (Status.PASS if marked else Status.FAIL)


@pytest.mark.parametrize("marked", [True, False])
def test_healthcheck_executes_failed_state_probe(tmp_path, marked):
    marker = tmp_path / ".hermes/gateway-stopped-by-release"
    marker.parent.mkdir()
    if marked:
        marker.write_text("stopped")
    script = f"""
source '{REPO}/automation/healthcheck_probes.sh'
valid_account() {{ return 0; }}
valid_unit() {{ return 0; }}
log() {{ printf '%s\\n' "$*"; }}
capture_on_node() {{
  local command="$2"
  command="${{command#*bash -c }}"
  HOME='{tmp_path}' bash -c "systemctl() {{ printf failed; return 3; }}; eval $command"
}}
probe_user_unit_active node agent hermes-gateway.service
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == (0 if marked else 1), result.stdout + result.stderr


def test_release_stop_resets_failed_before_inactive_check():
    text = (REPO / "automation/deploy_all.sh").read_text()
    stop = text[text.index("script='export XDG_RUNTIME_DIR"):text.index("\n    else", text.index("script='export XDG_RUNTIME_DIR"))]
    assert stop.index("systemctl --user stop ") < stop.index("systemctl --user reset-failed ")
    assert stop.index("systemctl --user reset-failed ") < stop.index("systemctl --user is-active ")
    assert " start " not in stop and " disable " not in stop

"""Exercise the actual release gate and v2 observation, without touching a node."""
import json
import subprocess
from dataclasses import replace

import pytest

from automation import deploy_all_observe, legacy_services as services, release_approval
from automation.deploy_declarations import parse_declaration_file
from automation.release_spec import LEGACY_SERVICE_LINES, legacy_service_surface
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_registry import _emit, _rows
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec


def test_plan_hash_and_card_cover_both_stops(monkeypatch, capsys):
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.legacy_service_off = list(services.UNITS)
    assert release_approval.cmd_plan(args) == 0
    payload = json.loads(capsys.readouterr().out)
    surfaces = [legacy_service_surface(name) for name in services.UNITS]
    assert all(list(item) in payload["surface_digests"] for item in surfaces)
    spec = _spec(*surfaces)
    assert all(LEGACY_SERVICE_LINES[name] in spec.render() for name in services.UNITS)
    assert spec.action_hash() != _spec().action_hash()


@pytest.mark.parametrize("name", list(services.UNITS))
@pytest.mark.parametrize("reaction,covered,head,expected", [
    ("approve", True, "a" * 40, 0), ("pending", True, "a" * 40, 1),
    ("both", True, "a" * 40, 8), ("approve", False, "a" * 40, 4),
    ("approve", True, "b" * 40, 4),
])
def test_existing_gate_requires_owner_head_and_exact_surface(
    tmp_path, monkeypatch, name, reaction, covered, head, expected,
):
    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests,
                                        *([legacy_service_surface(name)] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: owner if reaction in {"approve", "both"} else [],
        auth._CANCEL: owner if reaction == "both" else [],
    }, render_version=6)
    assert release_approval.main(["authorize-legacy-service", "--name", name, "--head", head]) == expected


def test_deploy_refuses_before_any_service_write_without_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    flag = tmp_path / ".hermes/release-flags/report-hub-off"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"off")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0 if argv[0] == "ssh" else 4,
                                           f"/srv/releases/{'a' * 40}\n", "")

    monkeypatch.setattr(services.subprocess, "run", run)
    assert services.deploy(tmp_path, "test-node", "/srv/current", "a" * 40) == 4
    assert len(calls) == 2
    assert not any("install" in " ".join(call) for call in calls)


@pytest.mark.parametrize("name,account", [("report-hub", "ops"), ("hermes-dashboard", "agent")])
@pytest.mark.parametrize("answer,expected", [("ok", "ok"), ("stale", "stale"), ("unknown", "unknown"), ("", "unknown")])
def test_declared_observation_checks_desired_mode_and_actual_units(tmp_path, name, account, answer, expected):
    declaration, = parse_declaration_file(
        "automation/legacy_services/deploy-manifest.txt",
        f"{account}|automation/legacy_services|.hermes/legacy-services/{name}.mode"
        "|v2:derived;algorithm=legacy-service-v1\n",
    )
    calls = []

    def runner(who, argv):
        calls.append((who, argv))
        return 0, answer

    seen = deploy_all_observe._derived(declaration, tmp_path, runner, {name: "off"})
    assert seen.state == expected
    assert calls == [(account, ("legacy-service", str(tmp_path), name, "off"))]


def test_report_probes_stay_retired_regardless_of_release_stop_marker(tmp_path):
    marker = services.marker_path(tmp_path, "report-hub")
    marker.parent.mkdir(parents=True)
    before = _rows(_emit(prelude=f'NODE_OPS_HOME="{tmp_path}"'))
    marker.write_bytes(b"off")
    after = _rows(_emit(prelude=f'NODE_OPS_HOME="{tmp_path}"'))
    assert before and not any("report-hub" in row for row in before)
    assert after == before


def test_registry_cannot_rotate_kanban_or_restart_released_off_report(tmp_path, monkeypatch):
    from automation.credential_rotation.registry import ROTATION_TARGETS, RotationError
    from automation.credential_rotation.rotate import rotate_target

    assert "kanban" not in ROTATION_TARGETS
    monkeypatch.setenv("HOME", str(tmp_path))
    marker = services.marker_path(tmp_path, "report-hub")
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"off")
    with pytest.raises(RotationError, match="stopped by release"):
        rotate_target("report-hub")


@pytest.mark.parametrize("state,status", [("inactive", "PASS"), ("active", "FAIL")])
def test_doctor_reports_only_a_verified_shutdown_as_expected(tmp_path, state, status):
    from automation.doctor.capabilities import evaluate
    from tests.unit.test_doctor import _connected_home, _facts, _run

    home = _connected_home(tmp_path)
    marker = services.marker_path(home, "hermes-dashboard")
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"off")
    facts = _facts(home, run=_run(gateway=state), online=False)
    finding = next(f for f in evaluate(facts) if f.key == "legacy-service:hermes-dashboard")
    assert finding.status.value == status

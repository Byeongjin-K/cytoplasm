"""Release/health contracts: use private temporary homes, never the real SSH directory."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation import deploy_all_config, omo_relay_release as release, release_approval
from automation.deploy_declarations import all_declarations
from automation.doctor import capabilities, facts
from automation.install.checks import Status
from automation.node_config import default_node_config
from automation.omo_relay import availability, client
from automation.release_spec import ReleaseSpec

REPO = Path(__file__).resolve().parents[2]


def route(home):
    return replace(default_node_config(), omo_relay_target="worker@relay.invalid",
                   omo_relay_key=home / ".ssh/omo_relay")


def test_switch_is_exact_and_missing_is_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "load_node_config", lambda: route(tmp_path))
    assert release.release_mode(tmp_path) == "off"
    flag = tmp_path / ".hermes/release-flags/omo-relay"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"on")
    assert release.release_mode(tmp_path) == release.digest(route(tmp_path))
    flag.write_bytes(b"on\n")
    with pytest.raises(ValueError):
        release.release_mode(tmp_path)


def test_install_creates_private_key_readback_and_revert_keeps_key(tmp_path, capsys):
    config = route(tmp_path)
    mode = release.digest(config)
    release.install(tmp_path, mode, release.payload(config))
    first = config.omo_relay_key.read_bytes()
    assert release.installed(tmp_path) == f"route={mode}"
    assert client.enabled(tmp_path)
    assert config.omo_relay_key.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr().out.startswith("ssh-ed25519 ")
    release.install(tmp_path, mode, release.payload(config))
    assert config.omo_relay_key.read_bytes() == first
    release.install(tmp_path, "off", "")
    assert not client.enabled(tmp_path)
    assert release.installed(tmp_path) == "route=off"
    assert config.omo_relay_key.read_bytes() == first


def test_unauthorized_deploy_has_no_ssh_write(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "release_mode", lambda _: "a" * 64)
    monkeypatch.setattr(release, "load_node_config", lambda: route(tmp_path))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 4)

    monkeypatch.setattr(subprocess, "run", run)
    assert release.deploy(tmp_path, "node.invalid", "/srv/runtime", "b" * 40) == 4
    assert len(calls) == 1
    assert calls[0][1:] == ["authorize-omo-relay", "--hash", "a" * 64, "--head", "b" * 40]


def test_config_observer_compares_installed_route_to_host_digest(tmp_path):
    decl = next(d for d in all_declarations(REPO) if d.attr("algorithm") == release.ALGORITHM)
    calls = []

    def run(account, argv):
        calls.append(argv)
        return 0, "OMO-RELAY have=route=off"

    _, _, state, _ = deploy_all_config.observe_config(
        decl, REPO, run, omo_relay_mode="a" * 64)
    assert state == "stale"
    assert calls == [("config", str(REPO), "automation.omo_relay_release")]


def test_release_card_binds_route_and_renders_switch_line():
    spec = ReleaseSpec("v3.0.0", "a" * 40, "b" * 32,
                       ((client.SURFACE, "c" * 64),), "Batch calls on workstation")
    legacy = replace(spec, surface_digests=())
    assert client.CARD_LINE in spec.render()
    assert client.CARD_LINE not in legacy.render()
    assert spec.action_hash() != legacy.action_hash()


def test_authorize_relay_delegates_to_existing_release_gate(monkeypatch):
    called = []
    monkeypatch.setattr(release_approval.skill_gate, "cmd_release_authorize",
                        lambda args: called.append(args) or 0)
    assert release_approval.main(["authorize-omo-relay", "--hash", "a" * 64, "--head", "b" * 40]) == 0
    assert called[0].surface == client.SURFACE


def test_three_failed_workers_notice_once_then_recovery(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(availability, "notify", lambda recovered: sent.append(recovered) or True)
    real_pid = os.getpid
    for worker in range(3):
        monkeypatch.setattr(availability.os, "getpid", lambda: real_pid() + worker)
        availability.record_result(tmp_path, False)
        availability.record_result(tmp_path, False)
    assert sent == [False]
    availability.record_result(tmp_path, True)
    availability.record_result(tmp_path, True)
    assert sent == [False, True]


def test_interleaved_retries_from_two_workers_do_not_make_three_ticks(tmp_path, monkeypatch):
    sent = []
    worker = [101]
    monkeypatch.setattr(availability.os, "getpid", lambda: worker[0])
    monkeypatch.setattr(availability, "notify", lambda recovered: sent.append(recovered) or True)
    for pid in (101, 102, 101, 102):
        worker[0] = pid
        availability.record_result(tmp_path, False)
    assert sent == []


def test_thirty_minutes_outage_and_failed_notice_retries(tmp_path, monkeypatch):
    sent = []
    clock = [0]
    monkeypatch.setattr(availability.time, "time", lambda: clock[0])
    monkeypatch.setattr(availability, "notify", lambda recovered: sent.append(recovered) or len(sent) > 1)
    availability.record_result(tmp_path, False)
    clock[0] = 1800
    availability.record_result(tmp_path, False)
    availability.record_result(tmp_path, False)
    availability.record_result(tmp_path, False)
    assert sent == [False, False]


def test_actual_local_response_fixture_is_the_client_wire_contract(tmp_path, monkeypatch):
    from tests.unit.test_omo_relay import relay_client

    wrapped = (Path(__file__).parent / "fixtures/omo-relay-answer.json").read_text()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, wrapped, ""))
    answer = relay_client(tmp_path, monkeypatch).complete_served("say 5")
    assert answer.text == "5"
    assert (answer.provider, answer.model) == tuple(json.loads(wrapped)[key] for key in ("provider", "model"))


def test_doctor_reports_relay_readiness_without_model_call(tmp_path, monkeypatch):
    config = route(tmp_path)
    monkeypatch.setattr(facts, "_timers", lambda *a: ())
    monkeypatch.setattr(client, "load_node_config", lambda *_args: config)
    root = tmp_path / ".hermes/omo-relay"
    root.mkdir(parents=True)
    (root / "mode").write_bytes(b"on")
    calls = []

    def run(argv, env):
        calls.append(argv)
        return (0, '{"ready":true}') if argv[0] == "ssh" else (0, "active")

    result = facts.gather(account="agent", role="agent", home=tmp_path,
                          gateway_unit="hermes-gateway.service", online=True, run=run)
    primary = next(f for f in capabilities.evaluate(result) if f.key == "primary-model")
    assert primary.status is Status.PASS
    assert result.omo_relay == "up"
    assert any(argv[0] == "ssh" for argv in calls)
    assert capabilities._primary(replace(result, omo_relay="down")).status is Status.FAIL

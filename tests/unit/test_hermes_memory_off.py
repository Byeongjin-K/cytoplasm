"""Q4 stops only the two memory timers and preserves unresolved owner choices."""
from dataclasses import replace
from pathlib import Path

import pytest

from automation import legacy_services as services
from automation.memory_relocate.model import RelocationState, record_key
from automation.memory_relocate.store import save_state
from tests.unit.test_memory_relocate_model import _record

NAME = "hermes-memory"
TIMERS = (
    "autophagy-cron-memory-curator-watch.timer",
    "autophagy-cron-memory-relocate-watch.timer",
)


def _state(home: Path, status: str) -> None:
    record = replace(_record(), status=status, message_id="123")
    save_state(home / ".hermes/memory-curator/relocations.json",
               RelocationState(1, {record_key(record.source_kind, record.entry_sha256): record}))


def _runner():
    states = {unit: ["enabled", "active"] for unit in TIMERS}
    calls = []

    def run(argv):
        calls.append(argv)
        verb, unit = argv[2], argv[-1]
        if verb in ("disable", "enable"):
            states[unit] = ["disabled", "inactive"] if verb == "disable" else ["enabled", "active"]
            return 0, ""
        output = states[unit][0 if verb == "is-enabled" else 1]
        return (0 if output in ("enabled", "active") else 3), output

    return states, calls, run


def test_memory_switch_requires_exact_off(tmp_path):
    assert services.release_mode(tmp_path, NAME) == "on"
    flag = tmp_path / ".hermes/release-flags/hermes-memory-off"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"off")
    assert services.release_mode(tmp_path, NAME) == "off"
    flag.write_bytes(b"off\n")
    with pytest.raises(ValueError):
        services.release_mode(tmp_path, NAME)


def test_stop_and_restore_only_timers_and_preserve_memory(tmp_path):
    states, calls, run = _runner()
    memory = tmp_path / ".hermes/memories"
    memory.mkdir(parents=True)
    for name in ("USER.md", "MEMORY.md", "USER.md.deleted-backup"):
        (memory / name).write_bytes(b"owner-memory")
    _state(tmp_path, "reconciled")
    assert services.install(tmp_path, NAME, "off", run) == 0
    assert services.released_off(tmp_path, NAME)
    assert states == dict.fromkeys(TIMERS, ["disabled", "inactive"])
    assert services.check(tmp_path, NAME, "off", run) == "ok"
    assert services.check(tmp_path, NAME, "on", run) == "stale"
    assert services.install(tmp_path, NAME, "on", run) == 0
    assert not services.released_off(tmp_path, NAME)
    assert states == dict.fromkeys(TIMERS, ["enabled", "active"])
    assert all(path.read_bytes() == b"owner-memory" for path in memory.iterdir())
    assert all(call[-1] in TIMERS for call in calls)


@pytest.mark.parametrize("status", ["proposed", "posted", "approved", "written", "ingested"])
def test_unresolved_cards_refuse_before_either_timer_changes(tmp_path, status):
    _state(tmp_path, status)
    _states, calls, run = _runner()
    with pytest.raises(ValueError, match="MEMORY-OFF-REFUSED.*미결"):
        services.install(tmp_path, NAME, "off", run)
    assert calls == []
    assert not services.released_off(tmp_path, NAME)


def test_unreadable_choices_refuse_without_any_stop(tmp_path):
    path = tmp_path / ".hermes/memory-curator/relocations.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    _states, calls, run = _runner()
    with pytest.raises(ValueError):
        services.install(tmp_path, NAME, "off", run)
    assert calls == []


def test_unmanaged_memory_timers_are_not_started(tmp_path):
    _states, calls, run = _runner()
    assert services.install(tmp_path, NAME, "on", run) == 0
    assert calls == []


@pytest.mark.parametrize("enabled,active", [("enabled", "inactive"), ("disabled", "active")])
def test_marker_does_not_hide_timer_drift(tmp_path, enabled, active):
    marker = services.marker_path(tmp_path, NAME)
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"off")
    assert services.check(tmp_path, NAME, "off", lambda argv: (
        0, enabled if argv[-2] == "is-enabled" else active,
    )) == "stale"


def test_release_plan_and_existing_card_bind_memory_stop(monkeypatch, capsys):
    import json

    from automation import release_approval
    from automation.release_spec import legacy_service_surface
    from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec

    _fake_plan(monkeypatch)
    args = _plan_args()
    args.legacy_service_off = [NAME]
    assert release_approval.cmd_plan(args) == 0
    payload = json.loads(capsys.readouterr().out)
    surface = legacy_service_surface(NAME)
    assert list(surface) in payload["surface_digests"]
    assert _spec(surface).action_hash() != _spec().action_hash()
    assert services.UNITS[NAME] == TIMERS


@pytest.mark.parametrize("active,expected", [("inactive", "PASS"), ("active", "FAIL")])
def test_doctor_expects_off_timers_but_still_detects_reenabled_timer(tmp_path, active, expected):
    from automation.doctor.capabilities import evaluate
    from tests.unit.test_doctor import _connected_home, _facts, _run

    home = _connected_home(tmp_path, jobs=[
        {"name": name, "enabled": True, "paused_at": "t", "last_status": "ok"}
        for name in ("memory-curator-watch", "memory-relocate-watch")
    ])
    marker = services.marker_path(home, NAME)
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"off")
    base = _run(gateway=active)

    def run(argv, env):
        return (1, "disabled") if "is-enabled" in argv else base(argv, env)

    facts = _facts(home, run=run, online=False)
    finding = next(f for f in evaluate(facts) if f.key == "legacy-service:hermes-memory")
    assert finding.status.value == expected
    assert next(f for f in evaluate(facts) if f.key == "scheduled-jobs").status.value == "PASS"


def test_live_watcher_lock_refuses_without_stopping_either_timer(tmp_path):
    import fcntl

    lock = tmp_path / ".hermes/memory-relocate/watch.lock"
    lock.parent.mkdir(parents=True)
    _states, calls, run = _runner()
    with lock.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="MEMORY-OFF-REFUSED.*실행 중"):
            services.install(tmp_path, NAME, "off", run)
    assert calls == []


def test_timer_convergence_honors_off_and_restoration(tmp_path):
    from automation.cron_timer import Job, Node, check, converge

    _states, calls, run = _runner()

    class FakeNode(Node):
        def unit(self, *args):
            return run(("systemctl", "--user", *args))

    node = FakeNode(tmp_path)
    assert services.install(tmp_path, NAME, "off", run) == 0
    calls.clear()
    job = Job("memory-curator-watch", "every 30m", "memory_curator_watch.py")
    assert check(node, job, root="/srv/current") == ("ok", "released-off")
    assert "released-off" in converge(node, job, root="/srv/current")
    assert all(call[2] in ("is-enabled", "is-active") for call in calls)
    assert services.install(tmp_path, NAME, "on", run) == 0
    assert check(node, job, root="/srv/current") == ("absent", "no-unit")


def test_cli_refuses_pending_cards_without_a_systemctl_call(tmp_path, monkeypatch, capsys):
    _state(tmp_path, "posted")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert services.main(["install", "--name", NAME, "--mode", "off"]) == 4
    assert "MEMORY-OFF-REFUSED: 미결 메모리 선택 1건" in capsys.readouterr().err


def test_deploy_refuses_memory_stop_before_stopping_other_legacy_services(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setenv("HOME", str(tmp_path))
    flags = tmp_path / ".hermes/release-flags"
    flags.mkdir(parents=True)
    for name in services.UNITS:
        (flags / f"{name}-off").write_bytes(b"off")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "ssh" and "install" in argv[-1]:
            return subprocess.CompletedProcess(argv, 4, "", "")
        return subprocess.CompletedProcess(argv, 0, f"/srv/releases/{'a' * 40}\n", "")

    monkeypatch.setattr(services.subprocess, "run", run)
    assert services.deploy(tmp_path, "test-node", "/srv/current", "a" * 40) == 5
    installs = [call[-1] for call in calls if call[0] == "ssh" and "install" in call[-1]]
    assert len(installs) == 1
    assert "--name hermes-memory" in installs[0]


@pytest.mark.parametrize("mode", ["on", "off"])
def test_release_observation_uses_off_readback_instead_of_active_timer(mode, tmp_path, monkeypatch):
    from automation import deploy_all_observe
    from automation.deploy_declarations import parse_declaration_file

    rows = (
        "agent|automation/legacy_services|.hermes/legacy-services/hermes-memory.mode"
        "|v2:derived;algorithm=legacy-service-v1\n"
        "agent|automation/legacy_services/x.py|memory-curator-watch"
        "|v2:cron;schedule=every 30m;script=memory_curator_watch.py;deliver=local;mode=no-agent;runner=systemd\n"
    )
    declarations = parse_declaration_file("automation/legacy_services/deploy-manifest.txt", rows)
    monkeypatch.setattr(deploy_all_observe, "all_declarations", lambda _root: declarations)
    calls = []

    def runner(account, argv):
        calls.append(argv)
        return 0, "stale" if argv[0] == "legacy-service" else "TIMER-CHECK state=ok detail=-\n"

    lines = deploy_all_observe.collect(tmp_path, runner, "ops", hermes_memory=mode)
    assert any("|derived|agent|.hermes/legacy-services/hermes-memory.mode|" in line
               and "|stale|" in line for line in lines)
    assert any(argv[0] == "timer" for argv in calls) == (mode == "on")
    assert ("legacy-service", str(tmp_path), NAME, mode) in calls


def test_real_cli_stops_and_restores_disabled_timers_without_deleting_files(tmp_path):
    import os
    import subprocess
    import sys

    from tests.unit.test_cron_timer import _exe, _SYSTEMCTL

    binary = tmp_path / "bin"
    binary.mkdir()
    _exe(binary / "systemctl", _SYSTEMCTL)
    environment = {**os.environ, "HOME": str(tmp_path), "FAKE_LOG": str(tmp_path / "calls"),
                   "FAKE_UNITS": str(tmp_path / "units.json"),
                   "PATH": f"{binary}:{os.environ['PATH']}"}
    for verb, mode, output in (("install", "off", ""), ("check", "off", "ok\n"),
                                ("install", "on", ""), ("check", "on", "ok\n")):
        result = subprocess.run(
            (sys.executable, "-m", "automation.legacy_services", verb, "--name", NAME, "--mode", mode),
            env=environment, capture_output=True, text=True, check=False, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == output
    assert not services.released_off(tmp_path, NAME)

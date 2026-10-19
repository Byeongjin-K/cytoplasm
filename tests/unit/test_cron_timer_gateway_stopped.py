"""Offline timer regression, separate to preserve FS3's existing test output hashes.

All commands use a temporary HOME and stub binaries; no live Hermes is invoked.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from automation import cron_timer

JOB = cron_timer.Job("sample-watch", "17 * * * *", "sample_watch.py")
TIMER = "autophagy-cron-sample-watch.timer"
ROOT = str(Path(__file__).resolve().parents[2])
HERMES = """#!/usr/bin/env python3
import json, os, pathlib, sys
home = pathlib.Path(os.environ["HOME"])
path = home / ".hermes/cron/jobs.json"
with (home / "calls").open("a") as log:
    log.write("hermes " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[2] == "list":
    print("  ⚠  Scheduler is not ready for profile '<profile>'")
    print("     Check status: hermes cron status")
    sys.exit(int(os.environ.get("LIST_RC", "1")))
if sys.argv[2] == "pause" and not os.environ.get("IGNORE_PAUSE"):
    raw = json.loads(path.read_text())
    jobs = raw["jobs"] if isinstance(raw, dict) else raw
    for job in jobs:
        if job["id"] == sys.argv[3]:
            job.update(enabled=False, state="paused", paused_at="2026-10-19T00:00:00Z")
    path.write_text(json.dumps(raw))
"""
SYSTEMCTL = """#!/usr/bin/env python3
import os, pathlib, sys
home = pathlib.Path(os.environ["HOME"])
args = sys.argv[2:]
with (home / "calls").open("a") as log:
    log.write("systemctl " + " ".join(args) + "\\n")
if args[0] == "enable":
    (home / "timer-on").touch()
elif args[0] == "is-enabled":
    print("enabled" if (home / "timer-on").exists() else "disabled")
elif args[0] == "is-active":
    print("active" if (home / "timer-on").exists() else "inactive")
"""


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> cron_timer.Node:
    home = tmp_path / "home"
    (home / ".hermes/cron").mkdir(parents=True)
    (home / ".hermes/scripts").mkdir()
    (home / ".hermes/scripts/sample_watch.py").write_text("print('ran')\n")
    (home / ".hermes/gateway-stopped-by-release").write_bytes(b"stopped")
    (home / ".hermes/cron/jobs.json").write_text(json.dumps({
        "jobs": [{"id": "ab12", "name": JOB.name, "enabled": True, "state": "scheduled"}],
    }))
    monkeypatch.setenv("HOME", str(home))
    for variable, name, body in (
        ("CRON_TIMER_HERMES", "hermes", HERMES),
        ("CRON_TIMER_SYSTEMCTL", "systemctl", SYSTEMCTL),
    ):
        binary = tmp_path / name
        binary.write_text(body)
        binary.chmod(0o700)
        monkeypatch.setenv(variable, str(binary))
    return cron_timer.Node(home)


@pytest.mark.parametrize("list_rc", ["0", "1"])
def test_converge_when_release_stopped_listing_is_unreadable(
    node: cron_timer.Node, monkeypatch: pytest.MonkeyPatch, list_rc: str,
) -> None:
    # Given a stopped gateway with an active persisted job.
    monkeypatch.setenv("LIST_RC", list_rc)
    # When the timer is converged.
    result = cron_timer.converge(node, JOB, root=ROOT)
    # Then pause precedes enable and the same source verifies check.
    calls = (node.home / "calls").read_text().splitlines()
    assert "hermes=paused" in result
    assert calls.index("hermes cron pause ab12") < calls.index(f"systemctl enable --now {TIMER}")
    assert cron_timer.check(node, JOB, root=ROOT) == ("ok", "-")


def test_pause_readback_when_offline_pause_does_not_stick(
    node: cron_timer.Node, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a CLI that reports success but leaves the job active.
    monkeypatch.setenv("IGNORE_PAUSE", "1")
    # When convergence attempts to pause.
    with pytest.raises(cron_timer.TimerError) as caught:
        cron_timer.converge(node, JOB, root=ROOT)
    # Then the timer cannot start.
    assert caught.value.rc == 5
    assert not (node.home / "timer-on").exists()


@pytest.mark.parametrize("marker", [None, b"stopped\n", b"running"])
def test_unreadable_listing_when_release_stop_is_not_proven(
    node: cron_timer.Node, marker: bytes | None,
) -> None:
    # Given readable job definitions but no exact release-stop marker.
    path = node.home / ".hermes/gateway-stopped-by-release"
    if marker is None:
        path.unlink()
    else:
        path.write_bytes(marker)
    # When convergence runs.
    with pytest.raises(cron_timer.TimerError) as caught:
        cron_timer.converge(node, JOB, root=ROOT)
    # Then the original fail-closed code remains.
    assert caught.value.rc == 4
    assert (node.home / "calls").read_text().splitlines() == ["hermes cron list --all"]


@pytest.mark.parametrize("payload", [
    "", "{}", '{"jobs": null}', '{"jobs": [null]}',
    '{"jobs": [{"name": "sample-watch"}]}',
    '{"jobs": [{"id": "ab12", "name": "sample-watch", "enabled": "false"}]}',
    '{"jobs": [{"id": "ab12", "name": "sample-watch", "paused_at": []}]}',
])
def test_untrusted_store_when_release_stopped(node: cron_timer.Node, payload: str) -> None:
    # Given malformed or ambiguous persisted job state.
    (node.home / ".hermes/cron/jobs.json").write_text(payload)
    # When convergence reads the offline source.
    with pytest.raises(cron_timer.TimerError) as caught:
        cron_timer.converge(node, JOB, root=ROOT)
    # Then no pause or timer mutation is allowed.
    assert caught.value.rc == 4
    assert (node.home / "calls").read_text().splitlines() == ["hermes cron list --all"]


@pytest.mark.parametrize("wrapped", [True, False])
def test_duplicate_names_when_release_stopped(node: cron_timer.Node, wrapped: bool) -> None:
    # Given two jobs with the same name in either supported store envelope.
    jobs = [{"id": key, "name": JOB.name, "enabled": False} for key in ("ab12", "cd34")]
    (node.home / ".hermes/cron/jobs.json").write_text(json.dumps({"jobs": jobs} if wrapped else jobs))
    # When convergence looks up the name.
    with pytest.raises(cron_timer.TimerError) as caught:
        cron_timer.converge(node, JOB, root=ROOT)
    # Then the existing duplicate-name refusal is preserved.
    assert caught.value.rc == 7


@pytest.mark.parametrize("fields", [
    {"state": "paused"}, {"paused": True}, {"enabled": True, "paused_at": "old"},
])
def test_enabled_job_when_only_cosmetic_pause_fields_exist(
    node: cron_timer.Node, fields: dict[str, str | bool],
) -> None:
    # Given state text that cannot prove scheduling is disabled.
    path = node.home / ".hermes/cron/jobs.json"
    path.write_text(json.dumps([{"id": "ab12", "name": JOB.name, **fields}]))
    # When job state is read.
    jobs = node.hermes_jobs(JOB.name)
    # Then it must still be paused through the CLI before starting a timer.
    assert jobs[0]["state"] == "active"


def test_cli_when_gateway_stopped_then_marker_removed(
    node: cron_timer.Node,
) -> None:
    # Given an isolated HOME and actual module entry point.
    args = ["--name", JOB.name, "--schedule", JOB.schedule, "--script", JOB.script]
    env = {**os.environ, "HOME": str(node.home)}
    # When the deployment and its verification run through the CLI.
    commands = [
        ["python3", "-m", "automation.cron_timer", verb, *args]
        for verb in ("converge", "check")
    ]
    results = [subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                              check=False, timeout=30) for command in commands]
    # Then convergence and check succeed without starting any gateway.
    assert [(r.returncode, r.stderr) for r in results] == [(0, ""), (0, "")]
    assert "state=ok" in results[1].stdout
    for command, result in zip(commands, results, strict=True):
        print("$ " + " ".join(command), result.stdout, result.stderr, f"rc={result.returncode}", sep="\n")
    node.home.joinpath(".hermes/gateway-stopped-by-release").unlink()
    closed = subprocess.run(commands[0], cwd=ROOT, env=env, capture_output=True, text=True,
                            check=False, timeout=30)
    print("$ marker absent: " + " ".join(commands[0]), closed.stdout, closed.stderr,
          f"rc={closed.returncode}", sep="\n")
    assert closed.returncode == 4


@pytest.mark.parametrize("wrapped", [True, False])
def test_absent_job_when_store_is_readable_and_empty(node: cron_timer.Node, wrapped: bool) -> None:
    # Given a positively readable empty store, not a missing file.
    (node.home / ".hermes/cron/jobs.json").write_text(json.dumps({"jobs": []} if wrapped else []))
    # When convergence runs.
    result = cron_timer.converge(node, JOB, root=ROOT)
    # Then an absent Hermes job is safe for timer execution.
    assert "hermes=absent" in result
    assert cron_timer.check(node, JOB, root=ROOT) == ("ok", "-")


@pytest.mark.parametrize("directory", [True, False])
def test_missing_or_unreadable_store_when_release_stopped(node: cron_timer.Node, directory: bool) -> None:
    # Given a source that cannot be read as a job database.
    path = node.home / ".hermes/cron/jobs.json"
    path.unlink()
    if directory:
        path.mkdir()
    # When convergence tries the fallback.
    with pytest.raises(cron_timer.TimerError) as caught:
        cron_timer.converge(node, JOB, root=ROOT)
    # Then source failure is not absence.
    assert caught.value.rc == 4


@pytest.mark.parametrize("fields", [
    {"enabled": False}, {"paused_at": "legacy"},
    {"enabled": False, "state": "completed"}, {"enabled": False, "paused_at": None},
])
def test_paused_job_when_persisted_state_proves_execution_stopped(
    node: cron_timer.Node, fields: dict[str, str | bool | None],
) -> None:
    # Given scheduling-disabled state or the runner's legacy pause marker.
    (node.home / ".hermes/cron/jobs.json").write_text(json.dumps([{"id": "ab12", "name": JOB.name, **fields}]))
    # When convergence reads it.
    cron_timer.converge(node, JOB, root=ROOT)
    # Then no pause or resume changes an already stopped job.
    assert "hermes cron pause ab12" not in (node.home / "calls").read_text()


def test_check_when_persisted_job_is_resumed_after_convergence(node: cron_timer.Node) -> None:
    # Given a converged timer.
    cron_timer.converge(node, JOB, root=ROOT)
    path = node.home / ".hermes/cron/jobs.json"
    path.write_text(json.dumps([{"id": "ab12", "name": JOB.name, "enabled": True}]))
    # When check reads the job again.
    result = cron_timer.check(node, JOB, root=ROOT)
    # Then no cached paused state hides a resumed job.
    assert result == ("stale", "hermes-job-active")

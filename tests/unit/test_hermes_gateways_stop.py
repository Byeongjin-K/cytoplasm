"""Release-host stop switch: owner binding, empty node HOME and reversible service actions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation import deploy_all, deploy_all_observe, deploy_all_probe, hermes_gateways, release_approval
from tests.unit import test_release_authorize as auth
from tests.unit.test_deploy_all_gateway_generation import _runtime, _sources, _wrap
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec

REPO = Path(__file__).resolve().parents[2]
SURFACE = ("hermes-gateways:stopped", hashlib.sha256(b"stopped").hexdigest())
CARD_LINE = "게이트웨이 멈춤: 노드 agent·peer Hermes 게이트웨이 정지(제거 아님, 파일 삭제 후 재배포로 다시 켬)"


@pytest.mark.parametrize("wanted,active,status", [
    ("stopped", True, "stale"), ("stopped", False, "ok"),
    ("running", True, "ok"), ("running", False, "stale"),
])
def test_node_probe_uses_host_gateway_mode_with_empty_node_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wanted: str, active: bool, status: str,
) -> None:
    # Given: no host flags or generation record exists in the node HOME.
    monkeypatch.setenv("HOME", str(tmp_path / "node-home"))
    runtime = _runtime(tmp_path)
    sources = _sources(runtime)
    seen: list[tuple[str, tuple[str, ...]]] = []

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        seen.append((account, args))
        if args[0] == "gateway-service":
            marker = "marked" if wanted == "stopped" else "unmarked"
            return (0, f"active\n{marker}\n") if active else (3, f"inactive\n{marker}\n")
        if args[0] == "gateway":
            name = runtime.name
            return 0, f"GATEWAY-GENERATION ok want={name} have={name} detail=-"
        if args[0] == "sha":
            return 0, sources[args[1]]
        return 0, "DELEGATED-END|0\n" if args[0] == "delegated" else ""

    # When
    lines = deploy_all_observe.collect(runtime, runner, "ops", hermes_gateways=wanted)
    plan = deploy_all.parse_observations(_wrap(lines))
    # Then
    services = [line.split("|") for line in lines if line.startswith("OBS|gateway-service|")]
    assert [(row[2], row[3], row[4]) for row in services] == [
        (account, status, wanted) for account in ("agent", "peer")
    ]
    assert plan.clean == (status == "ok")
    assert plan.hermes_gateways == wanted
    if wanted == "stopped" or not active:
        assert not [args for _account, args in seen if args[0] == "gateway"]
    actions = deploy_all.render_actions(plan)
    if status == "stale":
        verb = "stop" if wanted == "stopped" else "start"
        assert f"ACT|{verb}-gateway|agent+peer" in actions.splitlines()
    if wanted == "stopped":
        assert not plan.gateway_restart_needed
        assert "restart-gateway" not in actions
    if plan.clean:
        receipt = json.loads(deploy_all.render_receipt(plan, verified_at="t"))
        assert receipt["hermes_gateways"] == wanted
        assert receipt["surfaces"]["gateway_services"] == {
            account: wanted for account in ("agent", "peer")
        }


def test_probe_requires_explicit_host_gateway_mode() -> None:
    # Given / When / Then: even a valid dori mode cannot supply the missing stop mode.
    with pytest.raises(SystemExit) as raised:
        deploy_all_probe.main(["--dori-agent-chat", "off"])
    assert raised.value.code == 2


@pytest.mark.parametrize("value", [b"", b"running", b"stopped\n", b" stopped", b"STOPPED", b"\xff"])
def test_stop_switch_refuses_every_nonliteral_value(tmp_path: Path, value: bytes) -> None:
    # Given
    flag = tmp_path / ".hermes/release-flags/hermes-gateways-stopped"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(value)
    # When / Then
    with pytest.raises(ValueError, match="GATEWAY-STOP-REFUSED"):
        hermes_gateways.release_switch(tmp_path)


@pytest.mark.parametrize("mode", [None, "stopped"])
def test_release_plan_binds_only_explicit_gateway_stop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str | None,
) -> None:
    # Given
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.hermes_gateways = mode
    # When
    assert release_approval.cmd_plan(args) == 0
    payload = json.loads(capsys.readouterr().out)
    spec = _spec(*([SURFACE] if mode else []))
    # Then: shipped-copy equality and the existing renderer's frozen golden.
    assert payload["surface_digests"] == sorted([["repo", "c" * 64], *([list(SURFACE)] if mode else [])])
    assert spec.render().count(CARD_LINE) == (1 if mode else 0)
    if mode:
        assert spec.action_hash() != _spec().action_hash()
    else:
        assert hashlib.sha256(spec.render().encode()).hexdigest() == (
            "a3d23a31938565414513f51e72115cf36b2161c618223bcdf56b3d279127478d"
        )


@pytest.mark.parametrize("reaction,covered,head,code", [
    ("approve", True, "a" * 40, 0), ("pending", True, "a" * 40, 1),
    ("both", True, "a" * 40, 8), ("approve", False, "a" * 40, 4),
    ("approve", True, "f" * 40, 4),
])
def test_stop_authorization_uses_existing_owner_surface_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    reaction: str, covered: bool, head: str, code: int,
) -> None:
    # Given: the real surface/owner decision gate with only the Discord API replaced.
    base = auth._spec()
    bound = replace(base, surface_digests=(*base.surface_digests, *([SURFACE] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: bound)
    owner = [{"id": auth._OWNER, "bot": False}]
    reactions = {auth._APPROVE: owner if reaction in ("approve", "both") else [],
                 auth._CANCEL: owner if reaction == "both" else []}
    calls: list[str] = []
    auth._env(tmp_path, monkeypatch, reactions=reactions, api_log=calls, render_version=6)
    # When
    result = release_approval.main(["authorize-hermes-gateways-stopped", "--head", head])
    # Then
    assert result == code
    if code == 4:
        assert calls == []


def _host(tmp_path: Path, switch: str | None, auth_rc: int = 0) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    automation = repo / "automation"
    automation.mkdir(parents=True)
    for name in ("__init__.py", "hermes_agent_chat_ignore.py", "hermes_memory_config.py", "hermes_gateways.py", "legacy_services.py", "peer_retired.py", "hermes_product.py", "hermes_product_policy.py", "gateway_retirement.py", "gateway_retirement_policy.py", "owner_timers.py", "node_config.py"):
        (automation / name).symlink_to(REPO / "automation" / name)
    shutil.copyfile(REPO / "automation/deploy_all.sh", automation / "deploy_all.sh")
    (automation / "node_config_sh.py").write_text('print("")\n')
    authorize = automation / "release_approval_remote.sh"
    authorize.write_text('#!/bin/bash\nprintf "authorize %s\\n" "$*" >> "$CALLS"\n'
                         f'exit {auth_rc}\n')
    authorize.chmod(0o755)
    home = tmp_path / "host"
    home.mkdir()
    if switch is not None:
        flag = home / ".hermes/release-flags/hermes-gateways-stopped"
        flag.parent.mkdir(parents=True)
        flag.write_text(switch)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(
        '#!/bin/bash\ncmd="${*: -1}"\nprintf "%s\\n" "$cmd" >> "$CALLS"\n'
        'case "$cmd" in\n'
        'readlink*) printf "/srv/releases/%s\\n" "$HEAD";;\n'
        '*"--format actions"*) printf "%s\\n" "$ACTIONS"; exit 1;;\n'
        '*"--format gateways"*|*"--format report"*) exit 0;;\n'
        '*"--format receipt"*) printf \'{"hermes_gateways":"%s"}\' "$WANTED";;\n'
        '*"cat >"*"/receipt.json"*) cat > "$RECEIPT";;\n'
        '*"sha256sum"*"/receipt.json"*) sha256sum "$RECEIPT" | cut -d" " -f1;;\n'
        '*"systemctl --user stop"*|*"systemctl --user start"*|*"systemctl --user restart"*)\n'
        '  [[ "$cmd" != *"agent -H"* || "${FAIL_AGENT:-0}" != 1 ]];;\n'
        '*"gateways-stopped"*) ;;\n'
        '*"automation.owner_timers install"*) ;;\n'
        '*) exit 97;;\nesac\n'
    )
    ssh.chmod(0o755)
    git = bin_dir / "git"
    git.write_text('#!/bin/bash\nprintf "%s\\n" "$HEAD"\n')
    git.chmod(0o755)
    wanted = "stopped" if switch == "stopped" else "running"
    return repo, {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}",
                  "HEAD": "a" * 40, "DEPLOY_SSH_HOST": "fake-node",
                  "DEPLOY_ALL_WRAPPER_REFRESH_CMD": "true", "DEPLOY_ALL_RAG_MCP_REFRESH_CMD": "true",
                  "CALLS": str(tmp_path / "calls"), "RECEIPT": str(tmp_path / "receipt"),
                  "WANTED": wanted, "ACTIONS": f"ACT|{'stop' if switch else 'start'}-gateway|agent+peer"}


@pytest.mark.parametrize("switch,verb", [("stopped", "stop"), (None, "start")])
def test_deploy_passes_mode_to_every_probe_and_changes_both_services_only(
    tmp_path: Path, switch: str | None, verb: str,
) -> None:
    # Given: transport and authority are fakes; the host executes the real deploy orchestration.
    repo, env = _host(tmp_path, switch)
    if switch:
        env["ACTIONS"] += "\nACT|restart-gateway|agent+peer"  # a changed plugin must not wake the gateways
    # When
    result = subprocess.run(["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
                            env=env, capture_output=True, text=True, timeout=20, check=False)
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    probes = [line for line in calls if "deploy_all_probe.py" in line]
    assert probes
    assert all(f"--hermes-gateways {env['WANTED']}" in line and "--dori-agent-chat off" in line for line in probes)
    changes = [line for line in calls if f"systemctl --user {verb} " in line]
    assert len(changes) == 2
    assert "sudo -n -u agent " in changes[0] and "sudo -n -u peer " in changes[1]
    assert not any("disable" in line or "systemctl --user restart" in line for line in calls)
    if verb == "stop":
        assert not any("rm " in line for line in calls)
    assert any("--format gateways" in line for line in probes) == (verb == "start")
    assert json.loads((tmp_path / "receipt").read_text())["hermes_gateways"] == env["WANTED"]
    if switch:
        assert calls.index(f"authorize authorize-hermes-gateways-stopped --head {env['HEAD']}") < calls.index(changes[0])


@pytest.mark.parametrize("switch,auth_rc,marker", [
    ("stopped\n", 0, "GATEWAY-STOP-REFUSED"),
    ("stopped", 4, "GATEWAY-STOP-UNAUTHORIZED"),
    ("stopped", 1, "GATEWAY-STOP-UNAUTHORIZED"),
])
def test_deploy_refuses_bad_switch_or_unapproved_stop_before_mutation(
    tmp_path: Path, switch: str, auth_rc: int, marker: str,
) -> None:
    # Given
    repo, env = _host(tmp_path, switch, auth_rc)
    # When
    result = subprocess.run(["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
                            env=env, capture_output=True, text=True, timeout=20, check=False)
    # Then
    assert result.returncode == 4, result.stdout + result.stderr
    assert marker in result.stderr
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    assert "systemctl" not in calls and "--format actions" not in calls
    if marker == "GATEWAY-STOP-REFUSED":
        assert calls == ""


def test_stop_attempts_peer_even_when_agent_stop_fails(tmp_path: Path) -> None:
    # Given
    repo, env = _host(tmp_path, "stopped")
    env["FAIL_AGENT"] = "1"
    # When
    result = subprocess.run(["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
                            env=env, capture_output=True, text=True, timeout=20, check=False)
    # Then
    assert result.returncode == 1
    calls = (tmp_path / "calls").read_text()
    assert "sudo -n -u agent " in calls and "sudo -n -u peer " in calls
    assert not (tmp_path / "receipt").exists()


def test_stop_and_revert_execute_real_marker_and_systemctl_steps(tmp_path: Path) -> None:
    # Given: only systemd and SSH are replaced; the account script is actually executed.
    repo, env = _host(tmp_path, "stopped")
    ssh = tmp_path / "bin/ssh"
    old = ssh.read_text()
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, shlex, subprocess, sys\n"
        "parts = shlex.split(sys.argv[-1])\n"
        "if parts[:3] == ['sudo', '-n', '-u'] and 'systemctl' in parts[-1]:\n"
        "    account = parts[3]\n"
        "    home = pathlib.Path(os.environ['NODE_TEST_ROOT']) / account\n"
        "    home.mkdir(parents=True, exist_ok=True)\n"
        "    script = parts[-1]\n"
        "    env = {**os.environ, 'HOME':str(home)}\n"
        "    sys.exit(subprocess.run(['bash', '-c', script], env=env).returncode)\n"
        "sys.exit(subprocess.run(['bash', '-c', os.environ['FALLBACK_SSH'], '_', *sys.argv[1:]]).returncode)\n"
    )
    systemctl = tmp_path / "bin/systemctl"
    systemctl.write_text(
        '#!/bin/bash\ncase "$2" in\n'
        'stop) printf failed > "$HOME/service-state";;\n'
        'reset-failed) printf inactive > "$HOME/service-state";;\n'
        'start) printf active > "$HOME/service-state";;\n'
        'is-active) state="$(cat "$HOME/service-state")"; printf "%s\\n" "$state"; [[ "$state" == active ]];;\n'
        '*"gateways-stopped"*) ;;\n'
        '*) exit 97;;\nesac\n'
    )
    systemctl.chmod(0o755)
    env.update({"NODE_TEST_ROOT": str(tmp_path / "node"), "FALLBACK_SSH": old})
    # When: stop, then remove only the host switch and redeploy.
    stopped = subprocess.run(["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
                             env=env, capture_output=True, text=True, timeout=20, check=False)
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    for account in ("agent", "peer"):
        home = tmp_path / "node" / account
        assert (home / ".hermes/gateway-stopped-by-release").read_bytes() == b"stopped"
        assert (home / "service-state").read_text() == "inactive"
    (Path(env["HOME"]) / ".hermes/release-flags/hermes-gateways-stopped").unlink()
    env.update({"WANTED": "running", "ACTIONS": "ACT|start-gateway|agent+peer"})
    started = subprocess.run(["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
                             env=env, capture_output=True, text=True, timeout=20, check=False)
    # Then
    assert started.returncode == 0, started.stdout + started.stderr
    for account in ("agent", "peer"):
        home = tmp_path / "node" / account
        assert not (home / ".hermes/gateway-stopped-by-release").exists()
        assert (home / "service-state").read_text() == "active"

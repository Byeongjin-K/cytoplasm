"""The existing release gate binds retirement to the owner, surface and exact HEAD."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation import gateway_retirement as retirement, release_approval
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec

HEAD = "a" * 40
REPO = Path(__file__).resolve().parents[2]


def test_exact_switch_selects_the_bound_retirement_surface(tmp_path: Path) -> None:
    flag = tmp_path / ".hermes/release-flags/litellm"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"retired")
    assert retirement.release_switch(tmp_path) == "retired"
    assert retirement.surface() == ("litellm:retired", hashlib.sha256(b"retired").hexdigest())


@pytest.mark.parametrize("value", [b"", b"retired\n", b" retired", b"on", b"\xff"])
def test_switch_refuses_every_nonliteral_value(tmp_path: Path, value: bytes) -> None:
    flag = tmp_path / ".hermes/release-flags/litellm"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(value)
    with pytest.raises(ValueError, match="GATEWAY-RETIRE-REFUSED"):
        retirement.release_switch(tmp_path)


@pytest.mark.parametrize(("version", "card_digest"), [
    (6, "a3d23a31938565414513f51e72115cf36b2161c618223bcdf56b3d279127478d"),
    (7, "ac3d2206975a926b2493be154e3049c38f8b650eede85f5923ebc57b573d3b02"),
    (8, "ac3d2206975a926b2493be154e3049c38f8b650eede85f5923ebc57b573d3b02"),
])
def test_absent_switch_preserves_shipped_card_bytes_and_hash(
    tmp_path: Path, version: int, card_digest: str,
) -> None:
    # Digests captured from the unchanged b8a06c3 release renderer and this fixed fixture.
    assert retirement.release_switch(tmp_path) == "active"
    base = replace(_spec(), render_version=version)
    assert base.action_hash() == "fb17cdbb60ccb11840684527a1daeffc94c7b69b1e6035c10662c7f7143b6dfc"
    assert hashlib.sha256(base.render().encode()).hexdigest() == card_digest
    selected = replace(base, surface_digests=(*base.surface_digests, retirement.surface()))
    assert selected.action_hash() != base.action_hash()
    assert all(selected.render().count(line) == 1 for line in retirement.CARD_LINES)
    assert len(selected.render()) <= 1900


@pytest.mark.parametrize("selected", [False, True])
def test_plan_binds_only_explicit_retirement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], selected: bool,
) -> None:
    _fake_plan(monkeypatch)
    args = _plan_args()
    argv = ["plan", "--repo", args.repo, "--base", args.base, "--head", args.head,
            "--version", args.version]
    if selected:
        argv += ["--gateway-retirement", "retired"]
    assert release_approval.main(argv) == 0
    plan = json.loads(capsys.readouterr().out)
    assert (list(retirement.surface()) in plan["surface_digests"]) == selected


@pytest.mark.parametrize("reaction,covered,head,code", [
    ("approve", True, HEAD, 0), ("pending", True, HEAD, 1),
    ("both", True, HEAD, 8), ("approve", False, HEAD, 4),
    ("approve", True, "f" * 40, 4),
])
def test_owner_cancel_missing_surface_and_wrong_head_never_authorize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    reaction: str, covered: bool, head: str, code: int,
) -> None:
    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests,
                                        *([retirement.surface()] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    reactions = {auth._APPROVE: owner if reaction in ("approve", "both") else [],
                 auth._CANCEL: owner if reaction == "both" else []}
    auth._env(tmp_path, monkeypatch, reactions=reactions, api_log=[], render_version=6)
    assert release_approval.main(["authorize-gateway-retirement", "--head", head]) == code


@pytest.mark.parametrize(("selected", "auth_rc"), [(False, 0), (True, 0), (True, 4)])
@pytest.mark.parametrize("clean", [False, True])
def test_deploy_runs_removal_once_per_account_only_after_final_apply(
    tmp_path: Path, selected: bool, auth_rc: int, clean: bool,
) -> None:
    from tests.unit.test_hermes_gateways_stop import _host

    repo, env = _host(tmp_path, "stopped")
    if auth_rc:
        authorize = repo / "automation/release_approval_remote.sh"
        authorize.write_text(authorize.read_text().replace(
            "exit 0", '[[ "$1" != authorize-gateway-retirement ]] || exit 4\nexit 0'))
    if selected:
        (Path(env["HOME"]) / ".hermes/release-flags/litellm").write_bytes(b"retired")
    env.update(NODE_AGENT_ACCOUNT="agent", NODE_PEER_ACCOUNT="peer", NODE_OPS_ACCOUNT="ops",
               NODE_PRIVATE_ROOT=str(tmp_path / "private"))
    ssh = tmp_path / "bin/ssh"
    script = ssh.read_text().replace('*) exit 97;;',
                                    '*"automation.gateway_retirement --remove"*) exit 0;;\n*) exit 97;;')
    if clean:
        script = script.replace('printf "%s\\n" "$ACTIONS"; exit 1', 'exit 0')
    ssh.write_text(script)
    result = subprocess.run(("bash", str(repo / "automation/deploy_all.sh"), "--apply"),
                            env=env, text=True, capture_output=True, check=False, timeout=20)
    assert result.returncode == (4 if auth_rc else 0), result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    removals = [i for i, line in enumerate(calls) if "automation.gateway_retirement --remove" in line]
    assert len(removals) == (2 if selected and not auth_rc else 0)
    if removals:
        assert "authorize-gateway-retirement --head " + HEAD in calls[removals[0] - 1]
        for index, role in zip(removals, ("ops", "agent"), strict=True):
            assert f"sudo -n -u {role} -H" in calls[index]
            assert f"--role {role} --head {HEAD}" in calls[index]
        assert all("systemctl" not in line for line in calls[removals[-1] + 1:])


@pytest.mark.parametrize("selected", [False, True])
def test_release_host_passes_switch_only_when_selected(tmp_path: Path, selected: bool) -> None:
    from tests.unit.test_release_sh import _origin_with_commits, _run

    _origin, work = _origin_with_commits(tmp_path)
    if selected:
        flag = tmp_path / ".hermes/release-flags/litellm"
        flag.parent.mkdir(parents=True)
        flag.write_bytes(b"retired")
    result = _run(tmp_path, work, decisions="2 9")
    assert result.returncode == 9, result.stdout + result.stderr
    args = (tmp_path / "plan-args").read_text()
    assert ("--gateway-retirement retired" in args) == selected


def test_cli_release_switch_has_no_removal_effects(tmp_path: Path) -> None:
    result = subprocess.run(
        (os.sys.executable, "-B", "-m", "automation.gateway_retirement", "--release-switch"),
        cwd=REPO, env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, check=False,
        timeout=10,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "active\n", "")
    assert list(tmp_path.iterdir()) == []


def test_cli_refusal_never_leaks_secret_values(tmp_path: Path) -> None:
    flag = tmp_path / ".hermes/release-flags/litellm"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"private-value")
    result = subprocess.run(
        (os.sys.executable, "-B", "-m", "automation.gateway_retirement", "--release-switch"),
        cwd=REPO, env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, check=False,
        timeout=10,
    )
    assert result.returncode == 4
    assert len(result.stderr.splitlines()) == 1
    assert "private-value" not in result.stderr

"""Pre-card rehearsal (lead rule 2026-10-19): every removal check runs, nothing changes.

The owner pressed ✅ three times on v2.6.0/v2.6.1 because the node-side guards refused only
after the card. `deploy_all --rehearse` runs those guards first; these tests pin that each
`--check` reaches the same refusals and stops before its first write.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation import gateway_retirement as retirement
from automation import hermes_product as product
from automation import p5_remove
from tests.unit import test_gateway_retirement as gw
from tests.unit import test_hermes_product_retire as hp
from tests.unit import test_p5_removal as p5

HEAD = "a" * 40


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else b"<dir>"
            for p in sorted(root.rglob("*"))}


@pytest.mark.parametrize("role", ["ops", "agent"])
def test_gateway_check_changes_nothing_and_writes_no_marker(tmp_path: Path, role: str) -> None:
    home = gw._home(tmp_path)
    before = _snapshot(home)
    calls: list[tuple[str, ...]] = []
    retirement.remove(home, role, HEAD, runner=gw._runner(calls), check=True)
    assert _snapshot(home) == before
    assert not any(" rm " in " ".join(argv) or "disable" in argv or "daemon-reload" in argv
                   for argv in calls)


def test_gateway_check_still_refuses_a_live_unit(tmp_path: Path) -> None:
    home = gw._home(tmp_path)
    with pytest.raises(ValueError, match="GATEWAY-RETIRE-REFUSED"):
        retirement.remove(home, "ops", HEAD, runner=gw._runner([], "active"), check=True)


def test_product_check_writes_no_backup_and_removes_nothing(tmp_path: Path) -> None:
    home, runtime = hp._home(tmp_path)
    before = _snapshot(home)
    product.remove(home, HEAD, runtime=runtime, proc_reader=lambda: (),
                   verify_migration=lambda: False, check_only=True)
    assert _snapshot(home) == before


def test_product_check_still_refuses_a_live_target(tmp_path: Path) -> None:
    home, runtime = hp._home(tmp_path)
    target = home / product.TARGETS[0]
    with pytest.raises(ValueError, match="HERMES-PRODUCT-REFUSED"):
        product.remove(home, HEAD, runtime=runtime, proc_reader=lambda: (target,),
                       verify_migration=lambda: False, check_only=True)


@pytest.mark.parametrize("role", ["agent", "peer"])
def test_p5_check_only_lists_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    home, current = p5._home(tmp_path, role)
    before = _snapshot(home)
    calls: list[tuple[str, ...]] = []
    p5._commands(monkeypatch, current, calls)
    assert p5_remove.check(home, role, "hermes").startswith(f"P5-CHECK: role={role} 통과")
    assert _snapshot(home) == before
    assert all(argv[1:3] == ("cron", "list") for argv in calls)


def test_p5_check_still_refuses_an_unexpected_job_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, current = p5._home(tmp_path, "agent")
    current.pop(next(iter(current)))
    p5._commands(monkeypatch, current, [])
    with pytest.raises(ValueError, match="expected 30"):
        p5_remove.check(home, "agent", "hermes")


def _rehearse(tmp_path: Path, *, refuse: str = "") -> tuple[subprocess.CompletedProcess[str], list[str]]:
    from tests.unit.test_hermes_gateways_stop import _host

    repo, env = _host(tmp_path, "stopped")
    flags = Path(env["HOME"]) / ".hermes/release-flags"
    (flags / "hermes-product").write_bytes(b"retired")
    (flags / "litellm").write_bytes(b"retired")
    env.update(NODE_AGENT_ACCOUNT="agent", NODE_PEER_ACCOUNT="peer", NODE_OPS_ACCOUNT="ops",
               NODE_PRIVATE_ROOT=str(tmp_path / "private"))
    ssh = tmp_path / "bin/ssh"
    extra = (
        '*"mkdir /tmp/deploy-rehearse."*) cat >/dev/null;;\n'
        '*"rm -rf /tmp/deploy-rehearse."*) ;;\n'
        f'*"{refuse or "never-matches"}"*"--check"*) exit 4;;\n'
        '*"--check"*) ;;\n'
        '*) exit 97;;'
    )
    ssh.write_text(ssh.read_text().replace("*) exit 97;;", extra))
    result = subprocess.run(("bash", str(repo / "automation/deploy_all.sh"), "--rehearse"),
                            env=env, text=True, capture_output=True, check=False)
    return result, (tmp_path / "calls").read_text().splitlines()


def test_rehearsal_runs_every_approved_removal_check_from_the_staged_candidate(tmp_path: Path) -> None:
    result, calls = _rehearse(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-1] == f"REHEARSAL-OK head={HEAD[:12]} actions=1 steps=plan,hermes-product,litellm"
    assert not any(line.startswith("authorize ") for line in calls)
    removals = [line for line in calls if "--remove" in line]
    assert len(removals) == 3 and all(
        line.endswith(" --check") and "PYTHONPATH=/tmp/deploy-rehearse." in line for line in removals)
    assert any(line.startswith("rm -rf /tmp/deploy-rehearse.") for line in calls)
    assert not (tmp_path / "receipt").exists()


def test_a_refused_check_names_its_step_and_fails(tmp_path: Path) -> None:
    result, calls = _rehearse(tmp_path, refuse="automation.gateway_retirement")
    assert result.returncode == 4
    assert result.stdout.splitlines()[-1] == "REHEARSAL-REFUSED step=litellm rc=4"
    assert any(line.startswith("rm -rf /tmp/deploy-rehearse.") for line in calls)

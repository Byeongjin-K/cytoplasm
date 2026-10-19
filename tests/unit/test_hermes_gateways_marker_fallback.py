"""Receiptless stop policy and deploy ordering; a new file preserves FS3 evidence hashes."""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation import deploy_reconcile_cli as cli
from automation.hermes_gateways import stopped_in_receipt
from tests.unit.test_hermes_gateways_stop import _host
from tests.unit.test_release_rollback import (
    _PRIOR_SHA, _TARGET_SHA, _FakeCommands, _runtime,
)


@pytest.mark.parametrize("projected", (False, True))
def test_missing_receipt_uses_only_ops_projection(tmp_path: Path, projected: bool) -> None:
    # Given: account markers alone are inaccessible to the reconciler.
    private = tmp_path / "private"
    private.mkdir()
    for account in ("agent", "peer"):
        marker = tmp_path / account / ".hermes/gateway-stopped-by-release"
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b"stopped")
    if projected:
        (private / "gateway-stopped-by-release").write_bytes(b"stopped")
    # When
    stopped = stopped_in_receipt(private / "deploy-all/receipt.json", ("agent", "peer"))
    # Then
    assert stopped is projected


@pytest.mark.parametrize("payload", (b"", b"stopped\n", b"running", b"\xff"))
def test_malformed_projection_blocks_when_receipt_missing(tmp_path: Path, payload: bytes) -> None:
    # Given
    (tmp_path / "gateway-stopped-by-release").write_bytes(payload)
    # When / Then
    with pytest.raises(ValueError):
        stopped_in_receipt(tmp_path / "deploy-all/receipt.json", ("agent", "peer"))


@pytest.mark.parametrize("mode", ("running", "stopped"))
def test_existing_receipt_wins_over_malformed_projection(tmp_path: Path, mode: str) -> None:
    # Given
    receipt = tmp_path / "deploy-all/receipt.json"
    receipt.parent.mkdir()
    receipt.write_text(json.dumps({
        "version": 2, "release_sha": _PRIOR_SHA, "hermes_gateways": mode,
        "surfaces": {"gateway_services": {"agent": mode, "peer": mode}},
    }))
    (tmp_path / "gateway-stopped-by-release").write_bytes(b"broken")
    # When
    stopped = stopped_in_receipt(receipt, ("agent", "peer"))
    # Then
    assert stopped is (mode == "stopped")


def test_projection_does_not_need_a_peer_account(tmp_path: Path) -> None:
    # Given: no account home exists, including the retired peer.
    (tmp_path / "gateway-stopped-by-release").write_bytes(b"stopped")
    # When
    stopped = stopped_in_receipt(tmp_path / "deploy-all/receipt.json", ("agent", "absent-peer"))
    # Then
    assert stopped


@pytest.mark.parametrize("scenario", ((True, 0), (False, 0), (True, 23)))
def test_reconciler_retains_projection_through_update_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: tuple[bool, int],
) -> None:
    # Given
    projected, smoke_code = scenario
    runtime = _runtime(tmp_path)
    fake = _FakeCommands(runtime, smoke_code=smoke_code)
    config = replace(cli._NODE_CONFIG, private_root=tmp_path / "private")
    config.private_root.mkdir()
    if projected:
        (config.private_root / "gateway-stopped-by-release").write_bytes(b"stopped")
    monkeypatch.setattr(cli, "_NODE_CONFIG", config)
    monkeypatch.setattr(cli, "_RELEASE_RUNTIME", runtime)
    monkeypatch.setattr(cli, "run_converge", lambda _command: fake.converge())
    monkeypatch.setattr(cli, "_run_release_command", fake.run)
    monkeypatch.setattr(cli, "notify_owner", fake.notify)
    # When
    result = cli.run_release_update(_TARGET_SHA, _PRIOR_SHA)
    # Then
    assert (result == 0) is (smoke_code == 0)
    assert any(call[-1] == "restart" for call in fake.calls) is not projected


def _node(tmp_path: Path, switch: str | None) -> tuple[Path, dict[str, str]]:
    """Execute real remote shell effects with only SSH, sudo and systemd replaced."""
    repo, env = _host(tmp_path, switch)
    private = tmp_path / "private"
    private.mkdir()
    env.update({
        "NODE_PRIVATE_ROOT": str(private), "NODE_OPS_ACCOUNT": "ops",
        "NODE_AGENT_ACCOUNT": "agent", "NODE_PEER_ACCOUNT": "peer",
        "DEPLOY_ALL_RECEIPT_DIR": str(private / "deploy-all"),
        "NODE_TEST_ROOT": str(tmp_path / "node"),
        "EVENTS": str(tmp_path / "events"),
    })
    for account in ("agent", "peer", "ops"):
        (tmp_path / "node" / account).mkdir(parents=True)
    scripts = {
        "sudo": '#!/bin/bash\nacct="$3"; shift 4\n'
        'export HOME="$NODE_TEST_ROOT/$acct"\n[[ -d "$HOME" ]] || exit 67\nexec "$@"\n',
        "ssh": '#!/bin/bash\ncmd="${*: -1}"\ncase "$cmd" in\n'
        'readlink*) printf "/srv/releases/%s\\n" "$HEAD";;\n'
        '*"--format actions"*) [[ "${PLAN_TRANSPORT_FAIL:-0}" != 1 ]] || exit 255\n'
        '  prefix="${cmd%%python3 *}"\n'
        '  if [[ -n "$prefix" ]]; then bash -c "${prefix% && }" || exit; fi\n'
        '  printf "%s\\n" "$ACTIONS"; exit "${PLAN_RC:-1}";;\n'
        '*"--format gateways"*|*"--format report"*) exit 0;;\n'
        '*"--format receipt"*) printf \'{"hermes_gateways":"%s"}\' "$WANTED";;\n'
        '*"automation.p5_remove"*|*"&& ! getent passwd"*) exit 0;;\n'
        '*) bash -c "${cmd//bash -lc/bash -c}";;\nesac\n',
        "systemctl": '#!/bin/bash\nverb="$2"; acct="${HOME##*/}"\n'
        'policy=absent; [[ ! -f "$NODE_PRIVATE_ROOT/gateway-stopped-by-release" ]] || policy=stopped\n'
        'printf "%s %s %s\\n" "$acct" "$verb" "$policy" >> "$EVENTS"\n'
        '[[ "${FAIL_ACCOUNT:-}" != "$acct" || "$verb" != "${FAIL_VERB:-}" ]] || exit 1\n'
        'case "$verb" in\n'
        'stop) [[ "$policy" == stopped ]] || exit 98; printf inactive > "$HOME/state";;\n'
        'reset-failed) :;;\n'
        'start) printf active > "$HOME/state";;\n'
        'is-active) state="$(cat "$HOME/state")"; printf "%s\\n" "$state"; [[ "$state" == active ]];;\n'
        '*) exit 97;;\nesac\n',
    }
    for name, body in scripts.items():
        script = tmp_path / "bin" / name
        script.write_text(body)
        script.chmod(0o755)
    return repo, env


@pytest.mark.parametrize("failure", ("", "agent", "peer"))
def test_stop_projects_before_units_and_retains_policy_on_failure(
    tmp_path: Path, failure: str,
) -> None:
    # Given
    repo, env = _node(tmp_path, "stopped")
    env.update({"FAIL_ACCOUNT": failure, "FAIL_VERB": "stop"})
    # When
    result = subprocess.run(
        ["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
        env=env, capture_output=True, text=True, check=False, timeout=20,
    )
    # Then: even a partial stop must never permit a later restart.
    assert result.returncode == (1 if failure else 0), result.stderr
    projection = Path(env["NODE_PRIVATE_ROOT"]) / "gateway-stopped-by-release"
    assert projection.read_bytes() == b"stopped"
    assert projection.stat().st_mode & 0o777 == 0o600
    events = Path(env["EVENTS"]).read_text().splitlines()
    assert "agent stop stopped" in events and "peer stop stopped" in events


@pytest.mark.parametrize("failure", ("", "agent", "peer"))
def test_start_clears_projection_only_after_both_units_active(
    tmp_path: Path, failure: str,
) -> None:
    # Given
    repo, env = _node(tmp_path, None)
    projection = Path(env["NODE_PRIVATE_ROOT"]) / "gateway-stopped-by-release"
    projection.write_bytes(b"stopped")
    env.update({"FAIL_ACCOUNT": failure, "FAIL_VERB": "start"})
    # When
    result = subprocess.run(
        ["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
        env=env, capture_output=True, text=True, check=False, timeout=20,
    )
    # Then
    assert result.returncode == (1 if failure else 0), result.stderr
    assert projection.exists() is bool(failure)
    events = Path(env["EVENTS"]).read_text().splitlines()
    assert "agent start stopped" in events and "peer start stopped" in events
    if not failure:
        assert "agent is-active stopped" in events and "peer is-active stopped" in events


@pytest.mark.parametrize("retired", (False, True))
def test_already_clean_stop_apply_still_publishes_projection(
    tmp_path: Path, retired: bool,
) -> None:
    # Given: no gateway command is necessary, even with the peer account gone.
    repo, env = _node(tmp_path, "stopped")
    env["PLAN_RC"] = "0"
    if retired:
        (Path(env["HOME"]) / ".hermes/release-flags/peer-retired").write_bytes(b"retired")
        (Path(env["NODE_PRIVATE_ROOT"]) / "peer-retired").write_bytes(b"retired")
        (tmp_path / "node/peer").rmdir()
    # When
    result = subprocess.run(
        ["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
        env=env, capture_output=True, text=True, check=False, timeout=20,
    )
    # Then
    assert result.returncode == 0, result.stderr
    assert (Path(env["NODE_PRIVATE_ROOT"]) / "gateway-stopped-by-release").read_bytes() == b"stopped"
    assert not Path(env["EVENTS"]).exists()


@pytest.mark.parametrize("failure", ("write", "transport"))
def test_projection_write_failure_blocks_before_unit_changes(
    tmp_path: Path, failure: str,
) -> None:
    # Given: projection publication cannot succeed.
    repo, env = _node(tmp_path, "stopped")
    if failure == "write":
        (Path(env["NODE_PRIVATE_ROOT"]) / "gateway-stopped-by-release").mkdir()
    else:
        env["PLAN_TRANSPORT_FAIL"] = "1"
    # When
    result = subprocess.run(
        ["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
        env=env, capture_output=True, text=True, check=False, timeout=20,
    )
    # Then
    assert result.returncode == 4, result.stderr
    assert not Path(env["EVENTS"]).exists()

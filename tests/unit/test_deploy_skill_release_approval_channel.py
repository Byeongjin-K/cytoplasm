"""Exercise release channel binding separately from FS3-frozen regression files.

Run the deploy script's stage-3 shell and refresh functions with isolated account
and gate stubs; no SSH, Discord, real HOME, signing keys, or mounts are used.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest


DEPLOY = Path(__file__).resolve().parents[2] / "automation" / "deploy-skill.sh"
PARENT = "111111111111111111"
THREAD = "222222222222222222"


def _run_stage3(
    tmp_path: Path, release: bool, record_channel: str | None
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    script = DEPLOY.read_text(encoding="utf-8")
    config_start = script.index('DEPLOY_APPROVALS_CHANNEL_ID="$(run_as')
    config = script[config_start:script.index("\n", config_start)]
    request_start = script.index('if [[ "$RELEASE_APPROVAL" == 1 ]]; then', config_start)
    stage3 = script[request_start:script.index('[[ "$REQUEST_ONLY" == 1 ]]', request_start)]
    functions = script[
        script.index("peer_attest() {"):script.index("\nsync_ops_checkout_for_peer_attest()")
    ]
    pending = tmp_path / ".hermes" / "skill-gate" / "pending"
    pending.mkdir(parents=True)
    interop = tmp_path / ".hermes" / "interop"
    interop.mkdir()
    (interop / "config.json").write_text(
        json.dumps({"deploy_approvals_channel_id": PARENT}), encoding="utf-8"
    )
    if record_channel is not None:
        record = {"channel_id": record_channel} if record_channel else {}
        (pending / "release.json").write_text(json.dumps(record), encoding="utf-8")
    trace = tmp_path / "peer-cli.txt"
    harness = r"""
set -euo pipefail
SKILL=demo DIGEST=abc MESSAGE_ID=333333333333333333
DEPLOY_NONCE=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
NODE_AGENT_ACCOUNT=agent NODE_PEER_ACCOUNT=peer
NODE_RUNTIME_ROOT_SNIPPET=: NODE_PEER_ATTEST_MODE=signed
PEER_ATTEST_PUBLIC_KEY=/fake/peer.pub PEER_RETIRED=active
PERSONAL=0 ACTIVATE_MANAGED=0 FRESH=0
PROVENANCE_REQUEST_ARGS=()
log() { printf '%s\n' "$*" >&2; }
die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
cleanup_peer_staging() { :; }
cleanup_review_staging() { :; }
run_as() {
  if [[ "$1" == peer ]]; then
    printf '%s\n' "$2" >> "$HOME/peer-cli.txt"
    printf 'stub-signed-record\n'
  else
    bash -c "$2"
  fi
}
gate() {
  if [[ "$2" == request ]]; then
    printf '{"message_id":"333333333333333333","deploy_nonce":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n'
  elif [[ "$*" != *--peer-attest-mode* ]]; then
    printf '%s:%s\n' "$MESSAGE_ID" "$DEPLOY_NONCE"
  elif [[ -f "$HOME/gate-checked" ]]; then
    cat >/dev/null
    return 0
  else
    cat >/dev/null
    touch "$HOME/gate-checked"
    return 7
  fi
}
"""
    refresh = (
        'release_authorize_with_refresh "$DIGEST"'
        if release
        else 'check_with_attestation_refresh "$DIGEST"'
    )
    result = subprocess.run(
        ["bash", "-c", harness + functions + "\n" + config + "\n" + stage3 + "\n" + refresh,
         "deploy-skill.sh", *(["--release-approval"] if release else [])],
        env={**os.environ, "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
             "RELEASE_APPROVAL": "1" if release else "0"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    commands = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    print(f"deploy-skill.sh demo {'--release-approval' if release else '(per-skill)'}")
    print(f"fixed config channel A={PARENT}; bound release channel B={record_channel!r}")
    print(result.stderr, end="")
    for command in commands:
        print(command)
    print(f"exit={result.returncode}")
    return result, commands


@pytest.mark.parametrize("release", [True, False], ids=["release-thread", "per-skill-parent"])
def test_attestation_uses_bound_channel_when_release_approval(
    tmp_path: Path, release: bool
) -> None:
    # Given: the fixed parent channel differs from the release record's thread.
    expected = THREAD if release else PARENT

    # When: stage 3 attests and the gate requests one TTL refresh.
    result, commands = _run_stage3(tmp_path, release, THREAD)

    # Then: both initial and refreshed peer CLI calls use the appropriate binding.
    assert result.returncode == 0, result.stderr
    arguments = [shlex.split(command) for command in commands]
    assert [args[args.index("--channel-id") + 1] for args in arguments] == [expected] * 2
    assert "--refresh" not in arguments[0]
    assert "--refresh" in arguments[1]


@pytest.mark.parametrize("record_channel", ["", None], ids=["channel-absent", "record-absent"])
def test_release_fails_closed_when_bound_channel_unavailable(
    tmp_path: Path, record_channel: str | None
) -> None:
    # Given: a fixed config channel exists but the release has no usable channel.

    # When: stage 3 tries to attest against the release approval.
    result, commands = _run_stage3(tmp_path, True, record_channel)

    # Then: deployment stops before invoking the peer with an empty or parent channel.
    assert result.returncode != 0
    assert commands == []

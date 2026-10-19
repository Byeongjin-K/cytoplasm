"""One reconciliation tick, wired to the node. Runs as ops from a systemd timer.

MD-2. The decision lives in `deploy_reconcile`; this file owns only the edges — reading
the two shas, calling the privileged helper, persisting state, handing a notice to a
notifier. Keeping them apart is what lets "exactly one owner notice per incident" be a
unit test instead of a hope.

The notifier is deliberately still a seam. Whether the existing Ops bot DM path can
carry incident notices is unmeasured, and guessing would be worse than queueing: an
unconfigured notifier reports failure, `reconcile_tick` retains the notice in state, and
the next tick retries it. Nothing is lost, and the tick still converges — which is the
part that keeps prod correct. Wiring it is a separate, measured step (plan MD-2).
"""
from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final

from automation.deploy_reconcile import LOCK_CONTENTION_RC, reconcile_tick
from automation.deploy_reconcile_mirror import (
    mirror_state_from_verdict,
    probe_mirror_verdict,
    sync_mirror as _sync_mirror,
)
from automation.deploy_reconcile_state import DEFAULT_STATE_PATH, load_state, save_state
from automation.deploy_reconcile_trust import observe_stale_trust_key
from automation.deploy_reconcile_unsigned import IncidentRecorder, observe_release_backlog
from automation.deploy_update_channel import (
    UpdateChannelSource,
    read_roster_update_channel,
    save_update_channel_binding as _save_update_channel_binding,
)
from automation.hermes_gateways import stopped_in_receipt
from automation.node_config import load_node_config
from automation.node_config_state import unconfigured_reason
from automation.owner_notice import notify_owner
from automation.release_rollback import (
    Command,
    ReleaseEffects,
    ReleaseRuntime,
    ReleaseTransition,
    apply_release_update,
)
from automation.update_trust import TrustedUpdate, UpdateTrustError, resolve_signed_update
from automation.update_trust_stale import StaleTrustKey, find_stale_trust_key
from automation.update_trust_state import release_floor_path

#: The ONLY privileged command this tick may run. No arguments: the helper resolves
#: the configured signed-update policy itself, so there is nothing here for a caller to aim.
CONVERGE_HELPER: Final = "/usr/local/libexec/autophagy-converge-origin-main"

_NODE_CONFIG: Final = load_node_config()
MIRROR: Final = _NODE_CONFIG.deploy_checkout
RELEASE_POINTER: Final = _NODE_CONFIG.release_current
UPDATE_CHANNEL_STATE: Final = (
    _NODE_CONFIG.private_root / "deploy-reconcile" / "update-channel.json"
)
#: The anti-rollback anchor (C1). Shared verbatim with the root-owned helper, which
#: derives it from the same ops node configuration — one floor, both verifiers.
RELEASE_FLOOR: Final = release_floor_path(_NODE_CONFIG)
_UPDATE_CHANNEL_SOURCE: Final = UpdateChannelSource(
    roster_path=_NODE_CONFIG.agent_home / ".hermes" / "roster.yaml",
    agent_account=_NODE_CONFIG.agent_account,
)
_RELEASE_RUNTIME: Final = ReleaseRuntime(
    current=RELEASE_POINTER,
    store_root=_NODE_CONFIG.service_root,
    failed_state=_NODE_CONFIG.private_root / "deploy-reconcile" / "failed-release.json",
    release_helper=_NODE_CONFIG.libexec_dir / "autophagy-install-release",
    gateway_helper=_NODE_CONFIG.libexec_dir / "autophagy-gateway-pair",
    smoke_script=RELEASE_POINTER / "automation" / "release-smoke.sh",
)

_CONVERGE_TIMEOUT: Final = 900.0
_VERDICT_TIMEOUT: Final = 60.0
_GIT_TIMEOUT: Final = 30.0
_FF_PULL_TIMEOUT: Final = 120.0

#: The mirror verdict comes from the shell primitive healthcheck.sh and land.sh already
#: share. Giving this — the only path that WRITES to the checkout — its own private idea
#: of "safe to move" is how the next fault class added to the verdict would fail to reach
#: the one caller that must respect it.
_MIRROR_PROBE: Final = Path(__file__).resolve().parent / "checkout_mirror_probe.sh"
_BEHIND: Final = "mirror-behind"

MIRROR_IN_SYNC: Final = "in-sync"
MIRROR_PULLED: Final = "fast-forwarded"
MIRROR_PULL_FAILED: Final = "ff-pull-failed"
MIRROR_PROD_STALE: Final = "left-behind"


def converge_command() -> tuple[str, ...]:
    return ("sudo", "-n", CONVERGE_HELPER)


def current_release_sha(pointer: Path = RELEASE_POINTER) -> str:
    """The generation prod is actually running, or "" if there is not one.

    Absent and dangling both read as "not converged". Treating a broken pointer as a
    satisfied one would make the reconciler go quiet exactly when the node is damaged.
    """
    try:
        return pointer.resolve(strict=True).name
    except OSError:
        return ""


def candidate_update_sha(update_channel: str | None = None) -> str:
    """Resolve the SAME signature-bound target the root-owned helper re-verifies.

    Never policy-bound. `require_signed_updates` used to gate this call while the helper
    independently demanded a signature, so on 2026-08-21 the pre-gate happily handed an
    unsigned head to a helper that then refused it every tick — a convergence that could
    not succeed, counted as a hard failure. Both halves exist to close a TOCTOU window by
    verifying the same thing; they can only do that if they ask the same question.
    """
    update = resolve_signed_update(
        MIRROR,
        remote_url=update_channel,
        floor_path=RELEASE_FLOOR,
    )
    observe_stale_trust_key(_stale_trust_key(update, update_channel))
    return update.commit_sha


def _stale_trust_key(update: TrustedUpdate, update_channel: str | None) -> StaleTrustKey | None:
    """Observation only: a remote we cannot read again this tick is no warning and no block."""
    try:
        return find_stale_trust_key(MIRROR, update, remote_url=update_channel)
    except UpdateTrustError as error:
        print(f"[deploy-reconcile] UPDATE-TRUST-STALE-UNKNOWN {error}", file=sys.stderr)
        return None


def run_converge(command: Sequence[str] | None = None) -> int:
    """Return the helper's exit code. 5 means another convergence holds the lock."""
    # Match release-helper diagnostics without flooding the timer on success or contention.
    completed = _run(command or converge_command(), _CONVERGE_TIMEOUT)
    if completed is None or completed.returncode not in (0, LOCK_CONTENTION_RC):
        why = "could not be run" if completed is None else " ".join(completed.stderr.split())
        print(f"[deploy-reconcile] HELPER-FAILED converge: {why[:400]}", file=sys.stderr)
    return 1 if completed is None else completed.returncode


def _run(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    """None means the command could not be run at all — never an outcome to act on."""
    try:
        return subprocess.run(
            tuple(command), capture_output=True, text=True, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def roster_update_channel(path: Path | None = None) -> str | None:
    return read_roster_update_channel(_UPDATE_CHANNEL_SOURCE, path)


def persist_update_channel_binding(update_channel: str | None, path: Path) -> None:
    _save_update_channel_binding(update_channel, path)


def _run_release_command(command: Command, timeout: float) -> int:
    # Capturing is required (nothing here reads the helper's stdout) but DROPPING what was
    # captured cost three days: `gateway-pair restart` failed on every convergence
    # (2026-08-16, 2026-08-19) and the only trace was `reason=gateway-restart` in a state
    # file, with no line anywhere saying why. Re-emit rather than capture less. Only the
    # helper name and its verb are echoed — the rollback argv carries shas already in the
    # journal — and stderr is clipped to 400 chars so one chatty failure cannot flood a
    # timer that fires every two minutes.
    completed = _run(command, timeout)
    if completed is None or completed.returncode != 0:
        why = "could not be run" if completed is None else " ".join(completed.stderr.split())
        print(f"[deploy-reconcile] HELPER-FAILED {' '.join(command[-2:])}: {why[:400]}",
              file=sys.stderr)
    return 1 if completed is None else completed.returncode


def run_release_update(
    origin_sha: str,
    prior_sha: str,
    command: Sequence[str] | None = None,
) -> int:
    """Run the fixed converger and its mandatory post-transition transaction."""
    try:
        stopped = stopped_in_receipt(
            _NODE_CONFIG.private_root / "deploy-all" / "receipt.json",
            (_NODE_CONFIG.agent_account, _NODE_CONFIG.peer_account),
            _NODE_CONFIG.private_root / "deploy-all" / "gateways-stopped",
        )
    except (OSError, ValueError) as error:
        print(f"[deploy-reconcile] GATEWAY-RECEIPT-BLOCK {type(error).__name__}", file=sys.stderr)
        return 1
    return apply_release_update(
        ReleaseTransition(prior_sha=prior_sha, target_sha=origin_sha),
        replace(_RELEASE_RUNTIME, gateways_stopped=stopped),
        ReleaseEffects(
            converge=lambda: run_converge(command),
            run=_run_release_command,
            notify=notify_owner,
        ),
    )


def mirror_verdict(
    mirror: Path = MIRROR,
    probe: Path = _MIRROR_PROBE,
    *,
    update_channel: str | None = None,
) -> str:
    """The shared shell verdict for the checkout, or "" when it could not be taken."""
    return probe_mirror_verdict(
        mirror, probe, update_channel=update_channel, run=_run, timeout=_VERDICT_TIMEOUT
    )


def _observe_mirror_verdict(mirror: Path, probe: Path, update_channel: str | None) -> str:
    return mirror_verdict(mirror, probe, update_channel=update_channel)


def sync_mirror(
    origin_sha: str,
    *,
    mirror: Path = MIRROR,
    pointer: Path = RELEASE_POINTER,
    probe: Path = _MIRROR_PROBE,
    update_channel: str | None = None,
) -> str:
    """Carry the observation checkout forward without bypassing its safety verdict."""
    return _sync_mirror(
        origin_sha,
        mirror=mirror,
        pointer=pointer,
        probe=probe,
        update_channel=update_channel,
        current_release_sha=current_release_sha,
        run=_run,
        mirror_verdict=_observe_mirror_verdict,
        behind=_BEHIND,
        in_sync=MIRROR_IN_SYNC,
        pulled=MIRROR_PULLED,
        pull_failed=MIRROR_PULL_FAILED,
        prod_stale=MIRROR_PROD_STALE,
        git_timeout=_GIT_TIMEOUT,
        ff_pull_timeout=_FF_PULL_TIMEOUT,
    )


def _mirror_state(update_channel: str | None) -> str:
    """미러 판정을 통지 문구가 쓰는 한 낱말로 — 두 호출부가 같은 자를 쓴다."""
    return mirror_state_from_verdict(
        _observe_mirror_verdict(MIRROR, _MIRROR_PROBE, update_channel)
    )


def _incidents() -> IncidentRecorder:
    return IncidentRecorder(DEFAULT_STATE_PATH, notify_owner)


def main() -> int:
    # Fail loudly rather than converge on a guess. An unconfigured node cannot reach
    # its own origin, and the old behaviour was to skip with rc 0 forever — the state
    # file kept saying everything was fine while production sat frozen.
    unset = unconfigured_reason(_NODE_CONFIG)
    if unset is not None:
        print(f"[deploy-reconcile] NODE-CONFIG-UNSET {unset}", file=sys.stderr)
        return 1
    update_channel = roster_update_channel()
    try:
        target_sha = (
            candidate_update_sha()
            if update_channel is None
            else candidate_update_sha(update_channel)
        )
    except UpdateTrustError as error:
        print(f"[deploy-reconcile] UPDATE-TRUST-BLOCK {error} — skipping tick", file=sys.stderr)
        # UNSIGNED-HEAD 는 이제 "게시된 릴리스가 하나도 없다" 만 뜻한다 — 비교할 릴리스가 없으니
        # 백로그가 아니라 블록이고, 반복되면 skip 누적이 드리프트로 승격한다. 릴리스 뒤에 쌓인
        # 미배포 커밋의 다이제스트는 아래 성공 경로가 관측한다.
        _incidents().skip("update-trust-block")
        return 0
    if not target_sha:
        # One transport miss is not drift. Repeated identical misses are structurally
        # indistinguishable from a permanently unreachable update channel and are counted.
        print("[deploy-reconcile] update target unresolved — skipping tick", file=sys.stderr)
        _incidents().skip("update-target-unresolved")
        return 0
    try:
        persist_update_channel_binding(update_channel, UPDATE_CHANNEL_STATE)
    except OSError as error:
        print(
            f"[deploy-reconcile] UPDATE-CHANNEL-BINDING-BLOCK {error} — skipping tick",
            file=sys.stderr,
        )
        _incidents().skip("update-channel-binding-block")
        return 0
    state = load_state(DEFAULT_STATE_PATH)
    current_sha = current_release_sha()
    backlog = observe_release_backlog(
        MIRROR, target_sha, update_channel=update_channel, mirror_state=_mirror_state
    )
    updated = reconcile_tick(
        state,
        origin_sha=target_sha,
        current_sha=current_sha,
        now=time.time(),
        converge=lambda: run_release_update(target_sha, current_sha),
        deliver=notify_owner,
        backlog=backlog,
    )
    save_state(DEFAULT_STATE_PATH, updated)
    # After the state is durable, and never able to change this tick's outcome: the
    # observation post is not prod, so nothing about it may fail a reconciliation.
    mirror_result = (
        sync_mirror(target_sha)
        if update_channel is None
        else sync_mirror(target_sha, update_channel=update_channel)
    )
    print(f"[deploy-reconcile] mirror {mirror_result}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

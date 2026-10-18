"""Post-convergence gateway restart, smoke validation, and durable rollback."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, TypeAlias

from automation.deploy_reconcile import FAILED_RELEASE_RC, LOCK_CONTENTION_RC


Command: TypeAlias = tuple[str, ...]
JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
RunCommand: TypeAlias = Callable[[Command, float], int]
Converge: TypeAlias = Callable[[], int]
Notify: TypeAlias = Callable[[str], bool]


class JsonLoader(Protocol):
    def __call__(self, s: str) -> JsonValue: ...

_SHA_RE: Final = re.compile(r"^[0-9a-f]{40,64}$")
_STATE_VERSION: Final = 1
_GATEWAY_TIMEOUT: Final = 120.0
_SMOKE_TIMEOUT: Final = 900.0
_ROLLBACK_TIMEOUT: Final = 120.0
_JSON_LOADS: JsonLoader = json.loads


class FailedReleaseStateError(RuntimeError):
    """The durable failed-release fingerprint is malformed or cannot be persisted."""


class FailedReleasePhase(StrEnum):
    ROLLBACK_PENDING = "rollback-pending"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FailedRelease:
    failed_sha: str
    prior_sha: str
    reason: str
    phase: FailedReleasePhase
    notice_sent: bool


@dataclass(frozen=True, slots=True)
class ReleaseTransition:
    prior_sha: str
    target_sha: str


@dataclass(frozen=True, slots=True)
class ReleaseRuntime:
    current: Path
    store_root: Path
    failed_state: Path
    release_helper: Path
    gateway_helper: Path
    smoke_script: Path
    gateways_stopped: bool = False


@dataclass(frozen=True, slots=True)
class ReleaseEffects:
    converge: Converge
    run: RunCommand
    notify: Notify


def _valid_sha(value: str) -> str:
    if _SHA_RE.fullmatch(value) is None:
        raise FailedReleaseStateError("failed-release fingerprint contains an invalid sha")
    return value


def _text_field(raw: dict[str, JsonValue], name: str) -> str:
    value = raw[name]
    if not isinstance(value, str):
        raise FailedReleaseStateError("failed-release fingerprint text fields are invalid")
    return value


def load_failed_release(path: Path) -> FailedRelease | None:
    """Parse the durable fingerprint strictly; corruption must not permit a retry."""
    if not path.exists():
        return None
    try:
        raw = _JSON_LOADS(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FailedReleaseStateError(f"failed-release fingerprint is unreadable: {path}") from error
    if not isinstance(raw, dict) or set(raw) != {
        "version", "failed_sha", "prior_sha", "reason", "phase", "notice_sent",
    }:
        raise FailedReleaseStateError("failed-release fingerprint fields are invalid")
    version = raw["version"]
    failed_sha = _text_field(raw, "failed_sha")
    prior_sha = _text_field(raw, "prior_sha")
    reason = _text_field(raw, "reason")
    phase = _text_field(raw, "phase")
    notice_sent = raw["notice_sent"]
    if isinstance(version, bool) or version != _STATE_VERSION:
        raise FailedReleaseStateError("failed-release fingerprint version is invalid")
    if not isinstance(notice_sent, bool):
        raise FailedReleaseStateError("failed-release fingerprint notice flag is invalid")
    try:
        parsed_phase = FailedReleasePhase(phase)
    except ValueError as error:
        raise FailedReleaseStateError("failed-release fingerprint phase is invalid") from error
    return FailedRelease(
        failed_sha=_valid_sha(failed_sha),
        prior_sha=_valid_sha(prior_sha),
        reason=reason,
        phase=parsed_phase,
        notice_sent=notice_sent,
    )


def _save_failed_release(path: Path, failed: FailedRelease) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent.chmod(0o700)
    payload = json.dumps(
        {
            "version": _STATE_VERSION,
            "failed_sha": failed.failed_sha,
            "prior_sha": failed.prior_sha,
            "reason": failed.reason,
            "phase": failed.phase.value,
            "notice_sent": failed.notice_sent,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    descriptor, temporary = tempfile.mkstemp(dir=parent, prefix=".failed-release-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _ = stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def _current_sha(current: Path) -> str:
    try:
        return current.resolve(strict=True).name
    except OSError:
        return ""


def _gateway_command(runtime: ReleaseRuntime, action: str) -> Command:
    return ("sudo", "-n", str(runtime.gateway_helper), action)


def _rollback_command(runtime: ReleaseRuntime, failed: FailedRelease) -> Command:
    return (
        "sudo", "-n", str(runtime.release_helper), "rollback",
        "--failed-sha", failed.failed_sha,
        "--sha", failed.prior_sha,
        "--store-root", str(runtime.store_root),
    )


def _notice(failed: FailedRelease, recovery_complete: bool) -> str:
    recovery = "완료" if recovery_complete else "미완료 — 다음 tick에서 복구를 재시도합니다"
    return (
        "업데이트 검증 실패로 이전 릴리스 자동 롤백을 시작했습니다.\n"
        f"  실패 릴리스: {failed.failed_sha}\n"
        f"  복귀 릴리스: {failed.prior_sha}\n"
        f"  실패 단계: {failed.reason}\n"
        f"  게이트웨이 복구: {recovery}"
    )


def _deliver_once(failed: FailedRelease, effects: ReleaseEffects, recovered: bool) -> FailedRelease:
    if failed.notice_sent:
        return failed
    return replace(failed, notice_sent=effects.notify(_notice(failed, recovered)))


def _rollback(failed: FailedRelease, runtime: ReleaseRuntime, effects: ReleaseEffects) -> int:
    _save_failed_release(runtime.failed_state, failed)
    active = _current_sha(runtime.current)
    pointer_restored = active == failed.prior_sha
    if active == failed.failed_sha:
        pointer_restored = effects.run(
            _rollback_command(runtime, failed), _ROLLBACK_TIMEOUT,
        ) == 0
    gateways_recovered = False
    if pointer_restored:
        if runtime.gateways_stopped:
            gateways_recovered = True  # No gateway action was taken; retain the stopped policy.
        else:
            restarted = effects.run(_gateway_command(runtime, "restart"), _GATEWAY_TIMEOUT) == 0
            healthy = effects.run(_gateway_command(runtime, "health"), _GATEWAY_TIMEOUT) == 0
            gateways_recovered = restarted and healthy
    recovery_complete = pointer_restored and gateways_recovered
    updated = replace(
        failed,
        phase=FailedReleasePhase.FAILED if recovery_complete else FailedReleasePhase.ROLLBACK_PENDING,
    )
    updated = _deliver_once(updated, effects, recovery_complete)
    _save_failed_release(runtime.failed_state, updated)
    return FAILED_RELEASE_RC


def _resume_or_block(failed: FailedRelease, runtime: ReleaseRuntime, effects: ReleaseEffects) -> int:
    if failed.phase is FailedReleasePhase.ROLLBACK_PENDING:
        return _rollback(failed, runtime, effects)
    updated = _deliver_once(failed, effects, True)
    if updated != failed:
        _save_failed_release(runtime.failed_state, updated)
    return FAILED_RELEASE_RC


def _stranded(failed: FailedRelease, active: str) -> bool:
    """True when the live pointer is neither generation this fingerprint knows about.

    Events have overtaken the rollback — a human landing, an out-of-band converge — so
    `_rollback` can neither undo `failed_sha` nor return to `prior_sha`. Left alone it
    rewrites ROLLBACK_PENDING every tick and refuses every later sha forever (measured
    2026-08-16: three days of frozen convergence behind a fingerprint naming two shas
    production had long since moved past).
    """
    return active not in (failed.failed_sha, failed.prior_sha)


def _settle_stranded(
    failed: FailedRelease,
    transition: ReleaseTransition,
    runtime: ReleaseRuntime,
) -> FailedRelease | None:
    """Retire a stranded fingerprint, keeping only its duty to suppress a known-bad sha."""
    if failed.failed_sha != transition.target_sha:
        runtime.failed_state.unlink(missing_ok=True)
        return None
    settled = replace(failed, phase=FailedReleasePhase.FAILED)
    if settled != failed:
        _save_failed_release(runtime.failed_state, settled)
    return settled


def apply_release_update(
    transition: ReleaseTransition,
    runtime: ReleaseRuntime,
    effects: ReleaseEffects,
) -> int:
    """Converge once, validate the new runtime, or durably roll it back."""
    prior_failed = load_failed_release(runtime.failed_state)
    if prior_failed is not None and _stranded(prior_failed, _current_sha(runtime.current)):
        prior_failed = _settle_stranded(prior_failed, transition, runtime)
    if prior_failed is not None and prior_failed.phase is FailedReleasePhase.ROLLBACK_PENDING:
        return _rollback(prior_failed, runtime, effects)
    if prior_failed is not None and prior_failed.failed_sha == transition.target_sha:
        return _resume_or_block(prior_failed, runtime, effects)
    if _current_sha(runtime.current) != transition.prior_sha:
        return LOCK_CONTENTION_RC
    converge_rc = effects.converge()
    if converge_rc != 0:
        return converge_rc
    if _current_sha(runtime.current) != transition.target_sha:
        return 1
    restart_rc = (
        0 if runtime.gateways_stopped
        else effects.run(_gateway_command(runtime, "restart"), _GATEWAY_TIMEOUT)
    )
    reason = "gateway-restart"
    if restart_rc == 0:
        smoke = ("/usr/bin/bash", str(runtime.smoke_script))
        if runtime.gateways_stopped:
            smoke += ("--gateways-stopped",)
        smoke_rc = effects.run(smoke, _SMOKE_TIMEOUT)
        if smoke_rc == 0:
            runtime.failed_state.unlink(missing_ok=True)
            return 0
        reason = "deploy-smoke"
    failed = FailedRelease(
        failed_sha=transition.target_sha,
        prior_sha=transition.prior_sha,
        reason=reason,
        phase=FailedReleasePhase.ROLLBACK_PENDING,
        notice_sent=False,
    )
    return _rollback(failed, runtime, effects)

"""The audited abandon for a decided release approval — release kind's escape hatch.

승인 lifecycle 은 소유자가 결정한(취소 포함) 요청을 절대 파괴하지 않는다(L3). 그래서
소유자가 ⛔ 한 뒤 남은 ``pending/release.json`` 은 다음 ``release.sh`` 의 요청 게시를
``owner-decided`` 로 DEFER 시킨다. 결정은 이미 소비됐는데 파일만 남아 다음 릴리스를
막는 가용성 결함이다. 스킬 게이트에는 ``skill_gate_retire.abandon`` 이 있었고, 이 모듈이
release kind 의 같은 탈출구다 — 인가 경계는 하나도 넓히지 않는다.

A1  운영자가 이름 댄 version·head_sha·message_id 세 필드가 저장된 레코드와 **정확히**
    일치할 때만 움직인다. 맹목적 삭제는 없다(``skill_gate_retire`` R1).
A2  요청 경로가 잡는 것과 같은 키 리스 아래에서만 변경한다(R2).
A3  원 메시지·소유자 리액션은 바꾸지 않는다. archive 뒤 상태 회신만 best-effort로 추가한다.
A4  감사 줄을 fsync 한 뒤에야 레코드가 pending 을 떠난다. 크래시는 감사 없는 삭제가 아니라
    복구 가능한 레코드를 남긴다(R5).
A5  레코드는 지워지지 않고 release-history 규약대로 0600 archive 로 바이트 그대로 옮겨진다 —
    ⛔ 된 승인의 증적은 릴리스가 지나가도 남는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Final

from automation import skill_gate, skill_gate_retire
from automation.interop.approval_lifecycle import ApprovalRecordsError, ApprovalSurfaceError
from automation.interop.approval_types import Probe
from automation.release_released import correct_abandoned, is_released
from automation.release_retire import archive_bytes, retire_released_record
from automation.release_thread_notice import reply
from automation.interop.origin_notice import ThreadOutcome
from automation.release_spec import ReleaseSpec, ReleaseSpecError, spec_from_record
from automation.skill_gate_approval import SkillApprovalGate
from automation.skill_gate_request import lease
from automation.skill_gate_specs import mask

ABANDON_ARCHIVE_DIRNAME: Final = "release-abandoned"
ABANDON_REFUSAL_EXIT: Final = 1
#: ``ReleaseSpec.key()`` / ``record_name()`` — 요청 경로가 잡는 리스와 같은 키, 같은 파일.
RELEASE_KEY: Final = "release"


class ReleaseAbandon(StrEnum):
    """Every terminal state of an abandon attempt, as its machine-readable token."""

    ABANDONED = "abandoned"
    RECORD_ABSENT = "record-absent"
    IDENTITY_MISMATCH = "identity-mismatch"
    STORE_UNREADABLE = "store-unreadable"
    LEASE_HELD = "lease-held"
    AUDIT_FAILED = "audit-failed"
    ARCHIVE_FAILED = "archive-failed"


@dataclass(frozen=True, slots=True)
class ReleaseAbandonOrder:
    """The operator override: which decided release to retire, why, and on whose authority."""

    version: str
    head_sha: str
    message_id: str
    reason: str
    actor: str


@dataclass(frozen=True, slots=True)
class ReleaseAbandoned:
    """One abandon outcome mapped onto the stdout/stderr and exit-code contract."""

    outcome: ReleaseAbandon
    exit_code: int
    message: str
    archived: Path | None = None


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refused(outcome: ReleaseAbandon) -> ReleaseAbandoned:
    return ReleaseAbandoned(
        outcome, ABANDON_REFUSAL_EXIT, f"RELEASE-ABANDON-REFUSED reason={outcome.value}"
    )


def _matched(encoded: bytes, order: ReleaseAbandonOrder) -> dict[str, str] | ReleaseAbandon:
    """The stored record iff it STILL binds this exact (version, HEAD, message id)."""
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError:
        return ReleaseAbandon.STORE_UNREADABLE
    if not isinstance(decoded, dict):
        return ReleaseAbandon.STORE_UNREADABLE
    record = {str(name): str(value) for name, value in decoded.items()}
    named = (order.version, order.head_sha, order.message_id)
    if not all(named):
        return ReleaseAbandon.IDENTITY_MISMATCH
    stored = (
        record.get("version", ""),
        record.get("head_sha", ""),
        record.get("message_id", ""),
    )
    return record if stored == named else ReleaseAbandon.IDENTITY_MISMATCH


def _append_audit(path: Path, line: dict[str, str]) -> None:
    """Durable: the override reaches the disk before the record it retires moves."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _archive_name(head_sha: str, encoded: bytes) -> str:
    """release-history 규약(``{head}.json``)에 바이트 지문을 더한 이름.

    같은 HEAD 가 ⛔ 뒤 다시 요청되면 nonce 가 달라 레코드 바이트도 달라진다. 이름이
    HEAD 뿐이면 두 번째 abandon 이 충돌로 막혀 탈출구 자체가 막힌다 — 지문이 그 wedge 를
    없애고, 같은 바이트의 재실행은 여전히 같은 파일로 수렴한다(멱등).
    """
    return f"{head_sha}.{sha256(encoded).hexdigest()[:12]}.json"


def abandon(
    gate_dir: Path, order: ReleaseAbandonOrder, audit_path: Path
) -> ReleaseAbandoned:
    """Operator override: refuses on ANY field mismatch, audits first, never deletes the message."""
    record_path = gate_dir / "pending" / f"{RELEASE_KEY}.json"
    with lease(gate_dir).hold(RELEASE_KEY) as owned:
        if not owned:
            return _refused(ReleaseAbandon.LEASE_HELD)
        try:
            encoded = record_path.read_bytes()
        except FileNotFoundError:
            return _refused(ReleaseAbandon.RECORD_ABSENT)
        except OSError:
            return _refused(ReleaseAbandon.STORE_UNREADABLE)
        found = _matched(encoded, order)
        if isinstance(found, ReleaseAbandon):
            return _refused(found)
        name = _archive_name(order.head_sha, encoded)
        line = {
            "action_hash": found.get("action_hash", ""),
            "actor": order.actor,
            "archive": name,
            "event": "release-abandon",
            "head_sha": found["head_sha"],
            "key": RELEASE_KEY,
            "message_id": found["message_id"],
            "reason": order.reason,
            "timestamp": _utc_now(),
            "version": found["version"],
        }
        try:
            _append_audit(audit_path, line)
        except OSError:
            return _refused(ReleaseAbandon.AUDIT_FAILED)
        try:
            archived = archive_bytes(gate_dir / ABANDON_ARCHIVE_DIRNAME, name, encoded)
            record_path.unlink()
        except (ReleaseSpecError, OSError):
            return _refused(ReleaseAbandon.ARCHIVE_FAILED)
        # 승인 본문·감사는 보존하고 상태 답글 뒤에만 스레드를 닫는다.
        reply(found, order.reason, ThreadOutcome.EXPIRED)
        return ReleaseAbandoned(
            ReleaseAbandon.ABANDONED,
            0,
            f"RELEASE-ABANDONED version={order.version} head={order.head_sha[:12]}"
            f" message_id={mask(order.message_id)} archive={archived.name}",
            archived,
        )


def retire_previous(
    head: str, tip: str, gate_factory: Callable[[ReleaseSpec], SkillApprovalGate],
    released: Mapping[str, str] | None = None,
) -> int:
    """Retire executed approvals; abandon an overtaken approval only with an explicit tip."""
    released = released or {}
    if released:
        _ = correct_abandoned(skill_gate.GATE_DIR, released)
    path = skill_gate.GATE_DIR / "pending/release.json"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("RELEASE-RETIRE: no previous release record", file=sys.stderr)
        return 0
    except (OSError, json.JSONDecodeError):
        print("RELEASE-RETIRE-BLOCK: release record is unreadable", file=sys.stderr)
        return 4
    if not isinstance(decoded, dict):
        return 4
    record = {str(name): str(value) for name, value in decoded.items()}
    try:
        spec = spec_from_record(record)
        gate = gate_factory(spec)
        outstanding = gate.outstanding("release")
        decision = gate.probe(outstanding[0]) if outstanding else Probe.UNVERIFIABLE
    except (ReleaseSpecError, ApprovalRecordsError, ApprovalSurfaceError, OSError) as error:
        print(f"RELEASE-RETIRE-BLOCK: {error}", file=sys.stderr)
        return 4
    # 미결정·미확인 요청은 lifecycle 의 재사용/supersede 가 소유한다 — retire 가 여기서 막으면
    # '재실행=재개' 계약이 깨진다(2026-08-31 v1.0.139 재개 실측). retire 는 실행 완료(✅ + 서명
    # 태그)된 승인만 감사 archive 로 옮기고, tip 을 벗어난 승인만 아래 감사형 abandon 으로 보낸다.
    if is_released(record, released):
        try:
            archived = retire_released_record(
                path, skill_gate.GATE_DIR / "release-history", expected_head=spec.head_sha,
                decision=decision, released=True,
            )
        except (ReleaseSpecError, OSError) as error:
            print(f"RELEASE-RETIRE-BLOCK: {error}", file=sys.stderr)
            return 4
        print(f"RELEASE-RETIRE: archived {spec.head_sha[:12]} as released {spec.version}"
              f" (owner reaction now reads {decision.value})" if archived is not None
              else "RELEASE-RETIRE: no previous release record", file=sys.stderr)
        return 0
    if decision is not Probe.APPROVED:
        print("RELEASE-RETIRE: not an executed approval — leaving it to the request lifecycle", file=sys.stderr)
        return 0
    if tip and spec.head_sha not in (head, tip):
        order = ReleaseAbandonOrder(
            spec.version, spec.head_sha, record.get("message_id", ""),
            f"approved release superseded by origin/main {tip} (release.sh auto-recovery)",
            skill_gate_retire.actor(),
        )
        return emit(abandon(
            skill_gate.GATE_DIR, order, skill_gate_retire.abandon_log(skill_gate.APPROVAL_LOG),
        ))
    try:
        archived = retire_released_record(
            path, skill_gate.GATE_DIR / "release-history", expected_head=head, decision=decision,
        )
    except (ReleaseSpecError, OSError) as error:
        print(f"RELEASE-RETIRE-BLOCK: {error}", file=sys.stderr)
        return 4
    print(f"RELEASE-RETIRE: archived {head[:12]}" if archived is not None
          else "RELEASE-RETIRE: no previous release record", file=sys.stderr)
    return 0


def emit(result: ReleaseAbandoned) -> int:
    """Both halves are machine-readable: the override on stdout, the refusal on stderr."""
    print(result.message, file=sys.stdout if result.exit_code == 0 else sys.stderr)
    return result.exit_code


def command(args: argparse.Namespace) -> int:
    """Both CLIs share the same closure order, actor and audit destination."""
    order = ReleaseAbandonOrder(
        version=str(args.version),
        head_sha=str(args.head),
        message_id=str(args.message_id),
        reason=str(args.reason),
        actor=skill_gate_retire.actor(),
    )
    audit_path = skill_gate_retire.abandon_log(skill_gate.APPROVAL_LOG)
    return emit(abandon(skill_gate.GATE_DIR, order, audit_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-abandon",
        description="⛔ 된 release 승인 레코드를 감사와 함께 archive 로 놓아준다(메시지는 그대로).",
    )
    parser.add_argument("--version", required=True, help="레코드에 저장된 릴리스 버전")
    parser.add_argument("--head", required=True, help="레코드에 저장된 40자 HEAD sha")
    parser.add_argument("--message-id", required=True, help="소유자가 결정한 승인 메시지 id")
    parser.add_argument("--reason", required=True, help="감사에 남길 폐기 사유")
    return command(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

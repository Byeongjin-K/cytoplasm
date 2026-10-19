"""Byte-exact retirement of an executed release approval."""
from __future__ import annotations

import json
import os
import tempfile
import sys
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage, Ref

from automation import owner_notice, skill_gate
from automation.interop.approval_types import Probe
from automation.release_spec import ReleaseSpecError
from automation.skill_gate_request import lease


def archive_bytes(archive_root: Path, name: str, encoded: bytes) -> Path:
    """Move one approval record's exact bytes into a 0700 archive as a 0600 file.

    Single copy on purpose: the executed-release archive (``retire_released_record``)
    and the audited abandon (``release_abandon``) must write history identically —
    two archivers drift apart exactly where one of them stops being byte-exact.
    """
    archive_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive_root.chmod(0o700)
    target = archive_root / name
    if target.exists():
        if target.read_bytes() != encoded:
            raise ReleaseSpecError("release history conflicts with pending bytes")
        return target
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=archive_root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _approval_location(record: Mapping[str, str]) -> Ref:
    """저장된 카드 좌표만 사용한다. 공간 미상은 검색 키로 내려간다."""
    from automation.interop.owner_message import Ref

    guild = record.get("approval_guild_id") or None
    return Ref(
        scope="message", space="guild" if guild else "unknown", guild_id=guild,
        channel_id=record.get("channel_id") or None, message_id=record.get("message_id") or None,
        search=("Discord 검색", record["version"]),
    )


def stale_message(record: Mapping[str, str], tip: str) -> OwnerMessage | None:
    """관측 구간 없는 불일치 탐지 결과이며 릴리스 실행 완료가 아니다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    return OwnerMessage(
        subject_key=record["version"], subject="릴리스 승인 기준 불일치",
        fact=f"승인 기준 {record['head_sha'][:12]} ≠ origin/main {tip[:12]} · 자동 완결 보류",
        location=_approval_location(record),
        owner=Action(verb="reply", target=Ref(scope="self"), argument="automation/release.sh 실행 요청"),
        agent_next="재실행 시 옛 요청 감사 회수 후 새 승인 요청",
        recovery="not_applicable", detail=Result(outcome="executed"),
    )


def abandoned_message(record: Mapping[str, str], reason: str) -> OwnerMessage | None:
    """감사 회수의 결과 회신. 원 승인과 소유자 결정은 보존한다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    return OwnerMessage(
        subject_key=record["version"], subject="릴리스 승인 회수",
        fact=f"{' '.join(reason.split())[:1000]} · 감사 기록과 승인 원본 보존",
        location=_approval_location(record), owner=Action(verb="none"),
        agent_next="추가 실행 없음", recovery="not_applicable", detail=Result(outcome="cancelled"),
    )


def notify_stale_approval(record: Mapping[str, str], tip: str) -> None:
    """One owner notice per request identity, even while the destination tip advances."""
    identity = json.dumps([record["version"], record["head_sha"], record["message_id"]])
    key = sha256(identity.encode()).hexdigest()
    root = skill_gate.GATE_DIR / "release-stale-notified"
    try:
        with lease(skill_gate.GATE_DIR).hold("release-stale-notice") as owned:
            if not owned or (root / key).exists():
                return
            body = (
                f"⛔ 릴리스 {record['version']} 승인 기준 {record['head_sha'][:12]}가 "
                f"origin/main {tip[:12]}와 달라 자동 완결할 수 없습니다. "
                "automation/release.sh를 실행하면 옛 요청을 감사 회수하고 새 승인을 요청합니다."
            )
            message = stale_message(record, tip)
            if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
                ok = owner_notice.notify_owner(body, message=message, fault=True)
            else:
                ok = owner_notice.notify_owner(body, fault=True)
            if ok:
                archive_bytes(root, key, b"notified\n")
                print(f"RELEASE-STALE-NOTIFIED episode={key[:12]}", file=sys.stderr)
            else:
                print(f"RELEASE-STALE-NOTIFY-FAIL episode={key[:12]}", file=sys.stderr)
    except (OSError, ReleaseSpecError) as error:
        print(f"RELEASE-STALE-NOTIFY-FAIL {type(error).__name__}", file=sys.stderr)


def retire_released_record(
    record_path: Path,
    archive_root: Path,
    *,
    expected_head: str,
    decision: Probe,
    released: bool = False,
) -> Path | None:
    """Archive one executed release approval before the next request exists."""
    try:
        encoded = record_path.read_bytes()
        decoded = json.loads(encoded)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseSpecError("release record is unreadable") from error
    if not isinstance(decoded, dict):
        raise ReleaseSpecError("release record is not an object")
    head = str(decoded.get("head_sha", ""))
    if head != expected_head or len(head) != 40:
        raise ReleaseSpecError("pending release does not match the latest signed head")
    if decision is not Probe.APPROVED and not released:
        raise ReleaseSpecError("only an approved release can leave pending")
    target = archive_bytes(archive_root, f"{head}.json", encoded)
    record_path.unlink()
    return target

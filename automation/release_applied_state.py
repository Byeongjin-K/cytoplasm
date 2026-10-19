"""릴리스 적용 통지의 워크스테이션 절반 — 완결 마커 상태 파일과 한 틱의 판정·발신.

`release_applied_notice` 에서 떼어 낸 상태 파일 처리(`completed/`·`notified/`·`notify-skipped/`
마커)와 한 틱(`Sweep`)이다. 판정 규칙(`decide`)과 번호 출처(`release_tags`)는 통지 모듈이
소유하고 여기서는 부르기만 한다 — 그래서 그 모듈은 호출 시점에 가져온다(순환 import 방지).
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, assert_never

MODULE: Final = "automation.release_applied_notice"


def log(message: str) -> None:
    print(f"[release-complete] {message}")


def is_ancestor(repo: Path, sha: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ("git", "-C", str(repo), "merge-base", "--is-ancestor", sha, descendant),
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def ops_inbox() -> Path | None:
    """Where the operator's release items go instead of Discord (owner Q281, 2026-10-19).

    `RELEASE_OPS_INBOX`, else the path written in `~/.hermes/release-ops-inbox`; unset means the
    release log only. The path is per install, so it never lives in code.
    """
    configured = os.environ.get("RELEASE_OPS_INBOX", "").strip()
    if not configured:
        try:
            configured = (Path.home() / ".hermes" / "release-ops-inbox").read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return Path(configured).expanduser() if configured else None


def record_ops(version: str, sha: str, pending: tuple[str, ...]) -> None:
    """Release log lines plus one inbox line of kind `release-ops`; Discord gets none of them."""
    items = [line.strip() for line in pending if line.strip() and not line.strip().endswith(":")]
    if not items:
        return
    for item in items:
        log(f"RELEASE-OPS {version} {sha[:12]} {item}")
    inbox = ops_inbox()
    if inbox is None:
        return
    now = datetime.now(tz=UTC)
    row = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
        "id": f"release-ops-{int(now.timestamp() * 1000)}",
        "kind": "release-ops",
        "content": f"릴리스 {version} 적용 뒤 운영자 조치 (HEAD {sha[:12]}): " + " / ".join(items),
    }
    try:
        with inbox.open("a", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as error:
        log(f"RELEASE-OPS-INBOX-FAIL {type(error).__name__} {sha[:12]}")


def send_command(repo: Path) -> list[str]:
    configured = os.environ.get("RELEASE_APPROVAL_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    return [str(repo / "automation" / "release_approval_remote.sh")]


def mark(state: Path, kind: str, sha: str, body: str) -> None:
    directory = state / kind
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / sha
    _ = marker.write_text(f"{body}\n", encoding="utf-8")
    marker.chmod(0o600)


def unnotified(state: Path) -> tuple[str, ...]:
    completed = state / "completed"
    if not completed.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in completed.iterdir()
            if entry.is_file()
            and not (state / "notified" / entry.name).exists()
            and not (state / "notify-skipped" / entry.name).exists()
        )
    )


@dataclass(frozen=True, slots=True)
class Sweep:
    """한 틱분의 사실 — 완결 마커 목록을 이 노드 상태에 비추어 판정한다."""

    state: Path
    repo: Path
    pointer_sha: str | None
    receipt_sha: str | None
    pending: tuple[str, ...] = ()
    owner_pending: tuple[str, ...] = ()

    def handle(self, sha: str) -> None:
        from automation import release_applied_notice as notice

        facts = notice.ReleaseFacts(
            sha=sha,
            version_tags=notice.release_tags(self.repo, sha),
            pointer_sha=self.pointer_sha,
            receipt_sha=self.receipt_sha,
            superseded=self._superseded(sha),
        )
        decision = notice.decide(facts)
        match decision:
            case notice.Skip(reason):
                mark(self.state, "notify-skipped", sha, reason)
                log(f"RELEASE-APPLIED-SKIP {reason} {sha[:12]}")
            case notice.Retry(reason):
                log(f"RELEASE-APPLIED-RETRY {reason} {sha[:12]}")
            case notice.Applied(version):
                self._notify(sha, version)
            case unreachable:
                assert_never(unreachable)

    def _superseded(self, sha: str) -> bool:
        if self.pointer_sha is None or self.pointer_sha == sha:
            return False
        return is_ancestor(self.repo, sha, self.pointer_sha)

    def _notify(self, sha: str, version: str) -> None:
        from automation.release_applied_notice import release_summary

        owner = ("--owner-pending", json.dumps(list(self.owner_pending))) if self.owner_pending else ()
        summary = release_summary(sha)
        said = ("--summary", summary) if summary else ()
        completed = subprocess.run(
            (*send_command(self.repo), "send", "--version", version, "--head", sha, *owner, *said),
            env={**os.environ, "RELEASE_APPROVAL_MODULE": MODULE},
            check=False,
        )
        if completed.returncode != 0:
            log(f"RELEASE-APPLIED-SEND-FAIL rc={completed.returncode} {sha[:12]}")
            return
        stamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        mark(self.state, "notified", sha, f"{version} {stamp}")
        record_ops(version, sha, self.pending)
        log(f"RELEASE-APPLIED-NOTIFIED {version} {sha[:12]}")

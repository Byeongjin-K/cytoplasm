"""릴리스가 실제 운영환경에 적용되면 소유자에게 한 번 알린다 (2026-09-05 지시).

**승인 반응은 적용이 아니다.** ✅ 는 "잘라도 좋다"는 허락일 뿐이고, 태그가 잘린 뒤에도
전량 반영은 막힐 수 있다(샌드박스 블록·수렴 실패). 그래서 적용 판정의 근거를 셋으로
둔다 — 완결기의 성공 마커(`completed/<sha>`, `release.sh` + `deploy_all --apply` 성공
뒤에만 쓰인다) · 노드의 활성 릴리스 포인터 · 전량 재판정 영수증(RC-4). 셋이 모두 이
sha 를 가리킬 때만 "적용되었습니다"가 참이다.

**번호는 추정하지 않는다.** 그 sha 에 달린 릴리스 태그가 유일한 출처다. 없으면 다음
틱을 기다리고, 둘이면(실측: f00f334ca 의 v1.1.5+v1.2.0) 영구 보류로 남겨 사람이 고른다.

**지나간 릴리스는 알리지 않는다.** 완결 마커는 과거 sha 에도 남아 있다 — 활성 포인터의
조상이면 그 릴리스는 지금 돌고 있지 않으므로 SUPERSEDED 로 기록만 하고 보내지 않는다.

절반은 워크스테이션(`sweep`, 완결 타이머가 매 틱 호출)에서, 절반은 노드(`send`,
`release_approval_remote.sh` 가 agent 자격으로 실행)에서 돈다.

**목적지는 이 모듈이 정하지 않는다.** 2026-09-10 소유자 지시로 DM 전용
(`notify_owner_dm`)에서 `owner_notice.notify_owner` 로 옮겼다 — 파사드가
`owner_notice_channel_id`(#notifications)를 존중하고, 그 키가 없는 설치만 소유자 DM 으로
되돌린다(ON-1). 어느 쪽이든 DM 오픈은 파사드 안에서만 일어난다(ON-2/ON-3).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation import deploy_receipt, owner_notice, release_applied_state
from automation.interop.chunker import chunk_lines

_RELEASE_TAG: Final = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
_DEFAULT_RELEASE_CURRENT: Final = "/srv/autophagy-agent-current"
_MODULE: Final = "automation.release_applied_notice"
#: 남은 소유자 조치 한 조각의 글자 예산. 봉투(약 160자)·적용 문장·v2 인용 접두사(줄마다
#: 2자)를 더해도 Discord 한 메시지(2000자)에 들어가도록 잡았다 — 조치 줄 하나는 1000자 미만이다.
_PENDING_PIECE: Final = 1200


@dataclass(frozen=True, slots=True)
class Applied:
    """적용이 확인됐다 — 이 버전으로 통지한다."""

    version: str


@dataclass(frozen=True, slots=True)
class Skip:
    """다시 볼 필요가 없다 — 마커를 남겨 매 틱 되풀이하지 않는다."""

    reason: str


@dataclass(frozen=True, slots=True)
class Retry:
    """아직 모른다 — 마커를 남기지 않고 다음 틱에 다시 본다."""

    reason: str


Decision = Applied | Skip | Retry


@dataclass(frozen=True, slots=True)
class ReleaseFacts:
    """판정에 필요한 사실 전부 — 여기 없는 것은 판정에 쓰이지 않는다."""

    sha: str
    version_tags: tuple[str, ...]
    pointer_sha: str | None
    receipt_sha: str | None
    superseded: bool


def decide(facts: ReleaseFacts) -> Decision:
    """순수 판정. 순서가 곧 정책이다 — 앞선 조건이 뒤를 가린다."""
    if facts.superseded:
        return Skip("SUPERSEDED")
    if not facts.version_tags:
        return Retry("NO-TAG")
    if len(facts.version_tags) > 1:
        return Skip("VERSION-AMBIGUOUS")
    if facts.pointer_sha is None:
        return Retry("POINTER-UNREADABLE")
    if facts.pointer_sha != facts.sha:
        return Retry("POINTER-BEHIND")
    if facts.receipt_sha is None:
        return Retry("RECEIPT-MISSING")
    if facts.receipt_sha != facts.sha:
        return Retry("RECEIPT-STALE")
    return Applied(facts.version_tags[0])


def _split_probe(output: str) -> tuple[str | None, object]:
    """`readlink` 한 줄 + 영수증 JSON → (포인터 sha, 영수증 문서). 판독 불가는 None."""
    brace = output.find("{")
    head = (output if brace < 0 else output[:brace]).strip()
    pointer_line = head.splitlines()[-1].strip() if head else ""
    pointer = pointer_line.rsplit("/", 1)[-1] if "/" in pointer_line else None
    if brace < 0:
        return pointer, None
    try:
        return pointer, json.loads(output[brace:])
    except ValueError:
        return pointer, None


def parse_probe(output: str) -> tuple[str | None, str | None]:
    """`readlink` 한 줄 + 영수증 JSON → (포인터 sha, 영수증 sha). 판독 불가는 전부 None."""
    pointer, document = _split_probe(output)
    recorded = document.get("release_sha") if isinstance(document, dict) else None
    return pointer, recorded if isinstance(recorded, str) and recorded else None


def release_tags(repo: Path, sha: str) -> tuple[str, ...]:
    """그 커밋을 가리키는 릴리스 태그들 — prerelease 접미사는 번호가 아니다."""
    listed = subprocess.run(
        ("git", "-C", str(repo), "tag", "--points-at", sha),
        capture_output=True,
        text=True,
        check=False,
    )
    return tuple(
        name for line in listed.stdout.splitlines() if _RELEASE_TAG.match(name := line.strip())
    )


def _probe_command() -> list[str]:
    configured = os.environ.get("RELEASE_APPLIED_PROBE_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    from automation.node_config import load_node_config

    node = load_node_config()
    return [
        "ssh",
        node.deploy_ssh_host,
        f"readlink {node.release_current}; "
        f"sudo -n -u {node.ops_account} -H cat {node.private_root}/deploy-all/receipt.json",
    ]


def _probe() -> tuple[str | None, str | None, tuple[str, ...]]:
    """노드 사실을 한 번만 읽는다 — 포인터도 영수증도 노드 전역이라 sha 마다 찌를 이유가 없다."""
    result = subprocess.run(_probe_command(), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None, None, ()
    pointer, receipt = parse_probe(result.stdout)
    return pointer, receipt, deploy_receipt.pending_lines(_split_probe(result.stdout)[1])


def sweep(state: Path, repo: Path) -> None:
    """완결된 릴리스 중 아직 통지하지 않은 것만 판정한다. 대상이 없으면 노드도 찌르지 않는다."""
    candidates = release_applied_state.unnotified(state)
    if not candidates:
        return
    pointer_sha, receipt_sha, pending = _probe()
    tick = release_applied_state.Sweep(state, repo, pointer_sha, receipt_sha, pending)
    for sha in candidates:
        tick.handle(sha)


def _pending_facts(encoded: str) -> tuple[str, ...]:
    """워크스테이션이 영수증에서 만든 남은 소유자 조치 줄 — 목록이 아니면 확인 불가로 말한다.

    한 메시지에 다 들어가지 않으면 공용 줄 단위 분할(`chunk_lines`)로 나눠 이어지는 통지로
    보낸다. 전송 계층의 청킹은 2000자 지점에서 줄을 자르므로 여기서 먼저 줄 경계로 나눈다.
    """
    if not encoded:
        return ()
    try:
        lines = json.loads(encoded)
    except ValueError:
        lines = None
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        return ("남은 소유자 조치: 확인 불가 — 전달된 목록을 읽지 못했다",)
    body = lines[1:] if lines[:1] == ["남은 소유자 조치:"] else lines
    items = "\n".join(deploy_receipt.notice_line(line) for line in body)
    if not items:
        return ()
    return chunk_lines(
        items, limit=_PENDING_PIECE,
        header=lambda index, total: "남은 소유자 조치:" if total == 1 else f"남은 소유자 조치 ({index}/{total}):",
    )


def send(version: str, head: str, models: str = "", pending: str = "") -> int:
    """노드에서 도는 절반 — 포인터를 다시 확인하고 목적지는 파사드에 맡긴다."""
    del models  # Older workstation senders may still pass the retired Hermes summary.
    current = os.environ.get("NODE_RELEASE_CURRENT", "").strip() or _DEFAULT_RELEASE_CURRENT
    try:
        pointer = os.path.basename(os.readlink(current))
    except OSError:
        pointer = ""
    if pointer != head:
        print(
            f"[release-applied] APPLIED-POINTER-MISMATCH {pointer or 'unreadable'} "
            f"!= {head[:12]}",
            file=sys.stderr,
        )
        return 4
    from automation.release_applied_message import applied_message

    content = f"릴리스 {version} 가 적용되었습니다. (HEAD {head[:12]})"
    pieces = _pending_facts(pending)
    facts = (f"{content}\n{pieces[0]}", *pieces[1:]) if pieces else (content,)
    for fact in facts:
        message = applied_message(version, head, fact)
        if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
            delivered = owner_notice.notify_owner(fact, message=message)
        else:
            delivered = owner_notice.notify_owner(fact)
        if not delivered:
            return 3  # 첫 실패 조각에서 멈춘다 — 다음 틱이 전부 다시 보낸다(at-least-once)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-applied-notice")
    commands = parser.add_subparsers(dest="command", required=True)
    sweeper = commands.add_parser("sweep", help="완결된 릴리스의 적용 여부를 판정한다")
    _ = sweeper.add_argument("--state", required=True)
    _ = sweeper.add_argument("--repo", required=True)
    sender = commands.add_parser("send", help="소유자 DM 으로 적용 완료를 알린다")
    _ = sender.add_argument("--version", required=True)
    _ = sender.add_argument("--head", required=True)
    _ = sender.add_argument("--models", default="", help=argparse.SUPPRESS)
    _ = sender.add_argument("--pending", default="", help="영수증의 남은 소유자 조치 줄(JSON 목록)")
    arguments = parser.parse_args(argv)
    if arguments.command == "send":
        return send(
            str(arguments.version), str(arguments.head), str(arguments.models), str(arguments.pending)
        )
    try:
        sweep(Path(str(arguments.state)), Path(str(arguments.repo)))
    except Exception as error:  # noqa: BLE001 - 통지 실패가 완결을 막으면 본말전도다
        release_applied_state.log(f"RELEASE-APPLIED-SWEEP-FAIL {type(error).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

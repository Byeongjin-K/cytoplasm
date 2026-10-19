"""완결기가 **어느 릴리스를 완결할 것인가**를 정하는 단일 판정.

승인은 sha 에 묶이지만 `release_complete.sh` 는 매 틱 origin/main HEAD 하나만 들고 물었다.
그래서 태그를 자른 직후 새 커밋이 머지되면, 소유자 ✅ 를 받고 서명 태그까지 잘려 **노드가
실제로 돌리고 있는** 릴리스가 영영 완결되지 못한다 — 2026-09-08 v1.6.3 실측: 결정이 매 틱
`live request is bound to a different HEAD` 로 서서 전량 반영이 재개되지 않았고,
`completed/<sha>` 마커가 없어 `release_applied_notice` 스윕이 그 릴리스를 보지 못해 적용
완료 DM 도 유실됐다.

태그 후 대상은 **origin/main 에서 도달 가능한 가장 가까운 릴리스 태그의 커밋**이다.
태그 전에는 원격 승인 판정이 검증한 bound SHA·버전을 rc 3 후보로 내보낸다. 후보는
새 팁의 인가가 아니며, 워크스테이션이 조상 여부를 확인한 뒤 그 SHA만 태그·배포한다.
팁과 바인딩이 같으면 기존 결정 바이트·종료코드가 그대로이고, 이미 태그된 요청은
재인증·낡은 승인 통지 없이 기존 리컨실에 맡긴다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

from automation import skill_gate
from automation.interop.approval_lifecycle import ApprovalRecordsError, ApprovalSurfaceError, Probe
from automation.release_applied_notice import release_tags
from automation.release_spec import ReleaseSpec, ReleaseSpecError, spec_from_record
from automation.release_thread_notice import reply
from automation.interop.origin_notice import ThreadOutcome
from automation.release_retire import archive_bytes
from automation.skill_gate_request import lease
from automation.skill_gate_approval import SkillApprovalGate

DECISION_APPROVED: Final = 0
DECISION_UNAVAILABLE: Final = 2
DECISION_CANDIDATE: Final = 3  # Bound approval only; never approval of the requested tip.
DECISION_PENDING: Final = 7
DECISION_DENIED: Final = 9


def decision_exit(probe: Probe) -> int:
    """⛔ wins; everything that is not a definite owner answer stays pending."""
    if probe is Probe.APPROVED:
        return DECISION_APPROVED
    if probe is Probe.CANCELLED:
        return DECISION_DENIED
    return DECISION_PENDING


def approval_decision(
    args: argparse.Namespace,
    gate_factory: Callable[[ReleaseSpec], SkillApprovalGate],
    notify: Callable[[Mapping[str, str], str], None],
) -> int:
    """Resolve the live approval without changing its request lifecycle."""
    try:
        decoded = json.loads((skill_gate.GATE_DIR / "pending/release.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("RELEASE-DECISION: no live release request", file=sys.stderr)
        return DECISION_UNAVAILABLE
    except (OSError, json.JSONDecodeError):
        print("RELEASE-DECISION: release request record is unreadable", file=sys.stderr)
        return DECISION_UNAVAILABLE
    if not isinstance(decoded, dict):
        return DECISION_UNAVAILABLE
    record = {str(name): str(value) for name, value in decoded.items()}
    expected_head = str(getattr(args, "head", "") or "")
    tagged_head = str(getattr(args, "tagged", "") or "")
    mismatched = bool(expected_head and record.get("head_sha", "") != expected_head)
    if mismatched and tagged_head and record.get("head_sha", "") == tagged_head:
        print(
            f"RELEASE-DECISION: executed release {tagged_head[:12]} awaiting retirement",
            file=sys.stderr,
        )
        return DECISION_UNAVAILABLE
    candidate = bool(getattr(args, "completion_candidate", False))
    if mismatched and not (candidate or getattr(args, "notify_stale", False)):
        print("RELEASE-DECISION: live request is bound to a different HEAD", file=sys.stderr)
        return DECISION_UNAVAILABLE
    try:
        gate = gate_factory(spec_from_record(record))
        outstanding = gate.outstanding("release")
        if not outstanding:
            return DECISION_UNAVAILABLE
        probe = gate.probe(outstanding[0])
    except (ReleaseSpecError, ApprovalRecordsError, ApprovalSurfaceError, OSError) as error:
        print(f"RELEASE-DECISION: unverifiable ({type(error).__name__})", file=sys.stderr)
        return DECISION_UNAVAILABLE if mismatched else DECISION_PENDING
    if mismatched:
        branch = "candidate" if candidate else "notify-stale"
        print(f"RELEASE-DECISION: live request is bound to a different HEAD branch={branch} probe={probe.name.lower()}", file=sys.stderr)
        match probe:
            case Probe.APPROVED:
                if candidate:
                    print(f"{record['head_sha']} {record['version']}")
                    return DECISION_CANDIDATE
                notify(record, expected_head)
            case (
                Probe.BOUND_PENDING | Probe.CANCELLED | Probe.MISSING
                | Probe.BINDING_MISMATCH | Probe.UNVERIFIABLE
            ):
                return DECISION_UNAVAILABLE
            case unreachable:
                assert_never(unreachable)
        return DECISION_UNAVAILABLE
    print(f"RELEASE-DECISION: {probe.name.lower()} version={record['version']}", file=sys.stderr)
    match probe:
        case Probe.CANCELLED:
            # The decision is polled repeatedly; preserve one final reply per request.
            marker = skill_gate.GATE_DIR / "release-cancel-notified" / record["message_id"]
            try:
                with lease(skill_gate.GATE_DIR).hold("release-cancel-notice") as owned:
                    if owned and not marker.exists() and reply(record, "소유자 취소", ThreadOutcome.CANCELLED):
                        archive_bytes(marker.parent, marker.name, b"notified\n")
            except (OSError, ReleaseSpecError) as error:
                print(f"RELEASE-CANCEL-NOTICE-FAIL {type(error).__name__}", file=sys.stderr)
        case (Probe.APPROVED | Probe.BOUND_PENDING | Probe.MISSING | Probe.BINDING_MISMATCH | Probe.UNVERIFIABLE):
            pass
        case unreachable:
            assert_never(unreachable)
    return decision_exit(probe)

#: `git describe` 의 탐색 범위. 무엇이 릴리스 번호인지는 `release_tags` 가 단독으로 정한다.
_DESCRIBE_MATCH: Final = "v[0-9]*.[0-9]*.[0-9]*"


@dataclass(frozen=True, slots=True)
class Reconcile:
    """인가됐으나 완결되지 않은 릴리스 — 전량 반영과 완결 마커가 남았다."""

    sha: str
    version: str


@dataclass(frozen=True, slots=True)
class NoWork:
    """할 일 없음. 사유는 판독용이고 호출부는 조용히 넘어간다."""

    reason: str


Decision = Reconcile | NoWork


@dataclass(frozen=True, slots=True)
class CompletionFacts:
    """판정에 쓰이는 사실 전부 — 여기 없는 것은 판정에 쓰이지 않는다."""

    head: str
    tagged: str | None
    tag_names: tuple[str, ...]
    completed: bool


def decide(facts: CompletionFacts) -> Decision:
    """순수 판정. 순서가 곧 정책이라 앞선 조건이 뒤를 가린다."""
    if facts.tagged is None:
        return NoWork("NO-RELEASE-TAG")
    if facts.tagged == facts.head:
        return NoWork("TIP-IS-RELEASE")
    tags = facts.tag_names
    if len(tags) != 1:
        # describe 는 prerelease 접미 태그도 집어 오므로 릴리스 번호가 없을 수 있고,
        # 한 커밋에 번호가 둘이면 무엇을 완결했다고 적을지 사람이 골라야 한다.
        return NoWork("VERSION-AMBIGUOUS" if tags else "NO-RELEASE-TAG")
    if facts.completed:
        return NoWork("ALREADY-COMPLETED")
    return Reconcile(facts.tagged, tags[0])


def _git(repo: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _nearest_release_commit(repo: Path, head: str) -> str | None:
    """태그가 하나도 없는 이력은 사고가 아니라 정상이므로 None 으로 답한다."""
    described = _git(
        repo, "describe", "--tags", "--abbrev=0", "--match", _DESCRIBE_MATCH, head
    )
    if described is None:
        return None
    return _git(repo, "rev-parse", f"{described}^{{}}")


def gather(repo: Path, state: Path) -> CompletionFacts:
    """워크트리의 git 사실과 완결 마커만 읽는다 — 노드를 찌르지 않는다."""
    head = _git(repo, "rev-parse", "origin/main") or ""
    tagged = _nearest_release_commit(repo, head) if head else None
    return CompletionFacts(
        head=head,
        tagged=tagged,
        tag_names=release_tags(repo, tagged) if tagged else (),
        completed=bool(tagged) and (state / "completed" / str(tagged)).exists(),
    )


def tagged_release(repo: Path) -> str | None:
    """origin/main 에서 도달 가능한 가장 가까운 **릴리스 커밋** — 완결 여부와 무관하다.

    `decide` 의 대상 선정과 쓰임이 다르므로 판정도 다르다. 저기서는 "무엇을 완결했다고
    적을 것인가"를 물어 번호가 정확히 하나여야 하지만, 여기서 쓰는 사실은 "그 sha 가
    이미 잘린 릴리스인가" 하나뿐이다 — 한 커밋에 번호가 둘이어도 잘렸다는 사실은 변하지
    않는다. prerelease 접미 태그만 붙은 커밋은 릴리스가 아니므로 `release_tags` 가
    거른다(무엇이 릴리스 번호인지는 계속 그 함수가 단독으로 정한다).

    이 사실은 `release_approval decision --tagged` 로 건너가, **이미 실행된 릴리스**를
    낡은 승인으로 오인해 소유자에게 거짓 ⛔ 를 보내는 일을 막는다(2026-09-10 v1.6.7).
    """
    head = _git(repo, "rev-parse", "origin/main")
    if head is None:
        return None
    tagged = _nearest_release_commit(repo, head)
    if tagged is None or not release_tags(repo, tagged):
        return None
    return tagged


def main(argv: Sequence[str] | None = None) -> int:
    """rc 0 이면 stdout 에 `<sha> <version>`, rc 1 이면 사유만 — 호출부는 rc 로 분기한다.

    `--print-tagged` 는 그 대신 잘린 릴리스 커밋 하나만 찍는다(없으면 rc 1 에 무출력).
    완결기가 그 값을 argv 에 실어 나르므로 사유 문구를 섞지 않는다.
    """
    parser = argparse.ArgumentParser(prog="release-completion-target")
    _ = parser.add_argument("--repo", required=True)
    _ = parser.add_argument("--state", required=True)
    _ = parser.add_argument(
        "--print-tagged",
        action="store_true",
        help="도달 가능한 가장 가까운 릴리스 커밋만 출력한다(완결 여부와 무관)",
    )
    args = parser.parse_args(argv)
    if bool(args.print_tagged):
        tagged = tagged_release(Path(str(args.repo)))
        if tagged is None:
            return 1
        print(tagged)
        return 0
    decision = decide(gather(Path(str(args.repo)), Path(str(args.state))))
    match decision:
        case Reconcile(sha, version):
            print(f"{sha} {version}")
            return 0
        case NoWork(reason):
            print(reason)
            return 1
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    sys.exit(main())

"""v2 배포 선언 관측의 판정 — 트리·빌드·cron 의 어긋남과 릴리스가 올리지 못하는 표면.

`deploy_all_observe` 가 노드에서 내는 줄(`OBS|artifact…`·`OBS|artifact-detail…`·`OBS|pending…`·
`OBS|pending-detail…`·`OBS|pending-unknown…`, 마지막 `OBS|artifacts|<n>`)을 엄격하게 접는다.
형식이 틀리거나 개수가 맞지 않으면 판정이 아니라 `ObservationError` 다 — 잘린 관측을
깨끗함으로 읽으면 안 된다. 노드에서 온 owner 는 `<owner>/deploy.sh` 로만 실행되므로 저장소 안의
상대 경로 모양이 아니면 거부한다(관측 문자열이 실행할 경로를 고르지 못하게).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from automation.deploy_all import Plan

KINDS: Final = ("file", "tree", "derived", "cron", "config")
POLICIES: Final = ("required", "optional", "held", "retired")
ACTIVATIONS: Final = ("none", "gateway")
STATUSES: Final = (
    "ok", "stale", "absent", "extra", "unknown", "held", "retired-present", "retired-absent",
)
PENDING_STATUSES: Final = ("PASS", "FAIL", "UNKNOWN")
#: 배포기가 고칠 수 있는 어긋남. unknown 은 막되 배포기를 부르지 않는다(홈 행과 같은 규칙).
DEPLOYABLE: Final = frozenset({"stale", "absent", "extra"})
_DEFECTIVE: Final = DEPLOYABLE | {"unknown"}
_RETIRED: Final = frozenset({"retired-present", "retired-absent", "unknown"})
FAMILY: Final = frozenset(
    {"artifact", "artifact-detail", "pending", "pending-detail", "pending-unknown", "artifacts"}
)
_TOKEN: Final = re.compile(r"^[A-Za-z0-9_.-]+$")
_ACCOUNT: Final = re.compile(r"^[A-Za-z0-9_./-]+$")
#: 세그먼트가 영숫자·`_` 로 시작하므로 `.`·`..`·절대 경로·셸 문자가 들어올 자리가 없다.
_OWNER: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")
_COUNT: Final = re.compile(r"^[0-9]+$")


class ObservationError(RuntimeError):
    """관측이 불완전하거나 기형이다 — 잘린 관측을 깨끗함으로 읽으면 안 된다."""


@dataclass(frozen=True, slots=True)
class ArtifactState:
    kind: str
    account: str
    destination: str
    owner: str
    policy: str
    activation: str
    status: str
    want: str
    have: str
    details: tuple[tuple[str, str], ...] = ()

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.account, self.destination)


@dataclass(frozen=True, slots=True)
class PendingCheck:
    """릴리스 시점에 다시 돌린 위임 프로브 중 PASS 가 아닌 것 — 소유자만 닫을 수 있다."""

    probe: str
    node: str
    status: str
    name: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactObservations:
    artifacts: tuple[ArtifactState, ...]
    pending: tuple[PendingCheck, ...]
    delegated_checked: int
    pending_unknown: bool


def defective(state: ArtifactState) -> bool:
    return state.status in _DEFECTIVE


def _consistent(state: ArtifactState) -> bool:
    """상태가 신원과 정책에 맞는가 — "ok" 를 주장하면서 신원이 다르면 오도하는 관측이다."""
    if state.policy == "retired" or state.status in _RETIRED - {"unknown"}:
        return state.policy == "retired" and state.status in _RETIRED
    if state.policy == "held" and state.status in DEPLOYABLE:
        return False
    if state.status != "ok":
        return True
    if "?" in (state.want, state.have):
        return False
    return state.want == state.have or (state.policy == "optional" and state.have == "-")


def _artifact(fields: list[str]) -> ArtifactState:
    state = ArtifactState(*fields)
    valid = (
        state.kind in KINDS
        and _ACCOUNT.fullmatch(state.account) is not None
        and bool(state.destination)
        and _OWNER.fullmatch(state.owner) is not None
        and state.policy in POLICIES
        and state.activation in ACTIVATIONS
        and state.status in STATUSES
        and bool(state.want)
        and bool(state.have)
    )
    if not valid or not _consistent(state):
        raise ObservationError(f"malformed artifact observation: {'|'.join(fields)[:120]}")
    return state


def parse_artifact_lines(lines: Iterable[str]) -> ArtifactObservations:
    """`OBS|artifacts|<n>` 가 마지막이고 n 이 artifact 줄 수와 같아야 한다.

    이 종류의 줄이 하나도 없으면(v2 수집기가 붙기 전의 관측 모양) artifact 는 없고 위임 프로브는
    확인되지 않은 것(pending_unknown)이다 — 보지 못한 것을 PASS 로 세지 않는다.
    """
    artifacts: list[ArtifactState] = []
    checks: list[PendingCheck] = []
    passed = 0
    unknown = False
    count: int | None = None
    seen_any = False
    for line in lines:
        seen_any = True
        parts = line.split("|")
        kind = parts[1] if len(parts) > 1 else ""
        if count is not None:
            raise ObservationError(f"observation after OBS|artifacts: {line[:80]}")
        if kind == "artifact" and len(parts) == 11:
            state = _artifact(parts[2:])
            if any(other.key == state.key for other in artifacts):
                raise ObservationError(f"duplicate artifact observation: {line[:80]}")
            artifacts.append(state)
        elif kind == "artifact-detail" and len(parts) == 7 and all(parts[5:]):
            if not artifacts or artifacts[-1].key != (parts[2], parts[3], parts[4]):
                raise ObservationError(f"artifact detail without its artifact: {line[:80]}")
            last = artifacts[-1]
            artifacts[-1] = replace(last, details=(*last.details, (parts[5], parts[6])))
        elif (
            kind == "pending"
            and len(parts) == 6
            and _TOKEN.fullmatch(parts[2]) is not None
            and _TOKEN.fullmatch(parts[3]) is not None
            and parts[4] in PENDING_STATUSES
            and parts[5].strip()
        ):
            checks.append(PendingCheck(parts[2], parts[3], parts[4], parts[5]))
        elif kind == "pending-detail" and len(parts) == 5:
            if not checks or (checks[-1].probe, checks[-1].node) != (parts[2], parts[3]):
                raise ObservationError(f"pending detail without its check: {line[:80]}")
            last_check = checks[-1]
            checks[-1] = replace(last_check, details=(*last_check.details, parts[4]))
        elif kind == "pending-unknown" and len(parts) == 3 and parts[2]:
            unknown = True
        elif kind == "artifacts" and len(parts) == 3 and _COUNT.fullmatch(parts[2]):
            count = int(parts[2])
        else:
            raise ObservationError(f"malformed observation: {line[:80]}")
    if count is None:
        if seen_any:
            raise ObservationError("truncated observations: OBS|artifacts is missing")
        return ArtifactObservations((), (), 0, True)
    if count != len(artifacts):
        raise ObservationError(f"artifact count mismatch: declared {count}, saw {len(artifacts)}")
    for check in checks:
        passed += check.status == "PASS"
    return ArtifactObservations(
        tuple(artifacts),
        tuple(check for check in checks if check.status != "PASS"),
        passed,
        unknown,
    )


def owner_actions(plan: Plan) -> list[str]:
    """`ACT|owner|…` — 출력만 하는 소유자 조치. 오케스트레이터는 실행하지 않는다."""
    return [f"ACT|owner|{check.probe}@{check.node}: {check.name}" for check in plan.pending]


def render_artifact_lines(plan: Plan) -> list[str]:
    lines: list[str] = []
    for state in plan.artifacts:
        where = f"{state.kind} {state.account}:{state.destination}"
        if state.status == "unknown":
            lines.append(f"  ARTIFACT-UNKNOWN {where} — 읽지 못함(fail-closed)")
        elif state.status in DEPLOYABLE:
            lines.append(f"  ARTIFACT-{state.status.upper()} {where} → {state.owner}/deploy.sh")
        elif state.status in ("held", "retired-present"):
            lines.append(f"  ARTIFACT-{state.status.upper()} {where} (게이트를 막지 않음)")
        else:
            continue
        lines += [f"    {reason} {rel}" for reason, rel in state.details]
    for check in plan.pending:
        lines.append(f"  OWNER-ACTION {check.probe}@{check.node} [{check.status}]: {check.name}")
        lines += [f"    {detail}" for detail in check.details]
    if plan.pending_unknown:
        lines.append("  DELEGATED-UNKNOWN 위임 프로브를 릴리스 시점에 확인하지 못했다")
    return lines

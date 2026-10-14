"""전량 반영 영수증 v2 — 증명과 함께, 릴리스가 스스로 닫지 못한 소유자 조치를 적는다.

영수증은 "이 릴리스가 한 번 전량 반영되었다"의 롤아웃 증명이다(RC-4). clean 이 아닌 계획에는
서명하지 않는다. v2 는 그 옆에 보류(held)·퇴역(retired) 행과, 릴리스 시점에 다시 돌린 위임
프로브(root 자산·RAG·헬스체크 허용 목록) 가운데 PASS 가 아닌 것을 그 프로브 자신의 안내 줄과
함께 싣는다. RAG MCP는 승인된 추가 효과로 갱신하며 실패 결과도 남긴다. 위임 표면은 영수증을 막지 않는다 — 대신 적용 통지가
`pending_lines` 로 소유자에게 목록을 말한다. `release_sha` 는 최상위 키로 둔다(상시 프로브
`release_receipt_probe.sh` 가 그 키와 `version` 만 읽는다).
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from automation.deploy_all_gateway import receipt_held
from automation.deploy_all_kinds import KINDS, ArtifactState, ObservationError
from automation.rag_mcp_receipt import parse_refresh

if TYPE_CHECKING:
    from automation.deploy_all import Plan

RECEIPT_VERSION: Final = 2

#: 표면 ⑤(root 자산)·⑥(RAG)는 이 영수증이 직접 판정하지 않는다 — 상시 healthcheck
#: 프로브(release_helper_drift · rag_stack_current)가 대조하고, 릴리스 시점에는 그 프로브를
#: 다시 돌린 결과가 `Plan.pending` 으로 온다. 런타임 패키지는 v2 선언으로 직접 판정한다.
#: 여기 적는 이유는 영수증을 읽는 쪽이 "전량"의 경계를 오해하지 않게 하기 위해서다.
DELEGATED_SURFACES: Final = ("release-helpers", "rag-stack")

#: 상시 헬스체크가 보지 않고 릴리스 때만 판정하는 종류 — cron 등록, 게이트웨이 세대,
#: 그리고 중앙 표에 나오지 않는 v2 file 행.
JUDGED_AT_RELEASE_ONLY: Final = ("cron", "file", "gateway")

_ROSTER_GUIDANCE: Final = "roster 없음: python3 -m automation.group_roster init-local 로 roster 배치"
_ROSTER_UNVERIFIED: Final = "확인 불가: roster 검증을 실행하지 못했다"
#: 이 계정의 roster 는 유효하다 — 같은 묶음의 다른 계정이 풀리면 함께 반영된다(이 계정에 할 일 없음).
_ROSTER_SIBLING: Final = "다른 계정 조치 대기: 같은 묶음의 다른 계정 roster 가 해결되면 함께 반영된다"
_MAX_TEXT: Final = 300
#: 이미 무력화된 `@`(뒤에 ZWSP)는 건드리지 않는다 — 워크스테이션과 노드가 두 번 거쳐도 같다.
_MENTION: Final = re.compile("@(?!\u200b)")


#: roster 보류는 선언 사유가 아니라 관측이 정한다(roster 를 놓아야 풀린다).
_OBSERVED_REASONS: Final = frozenset({"roster-required", "roster-unverified", "roster-sibling"})


def _held_reason(state: ArtifactState, declared: Mapping[tuple[str, str, str], str]) -> str:
    observed = state.details[0][0] if state.details else "held"
    if observed in _OBSERVED_REASONS:
        return observed
    return declared.get(state.key) or observed


#: 릴리스 ✅ 로 인가된 헬스체크 허용 목록 래퍼 갱신의 결과 토큰 → 소유자 문장(healthcheck_wrapper_release.sh).
_WRAPPER_REASONS: Final = {
    "not-covered": "이 릴리스 승인이 지금 기대 지문을 덮지 않는다(다음 릴리스 카드에 실린다)",
    "cancelled": "소유자 ⛔",
    "approval-unverified": "릴리스 승인을 확인하지 못했다",
    "expected-unknown": "릴리스가 기대하는 지문을 계산하지 못했다",
    "installed-unreadable": "설치된 래퍼를 읽지 못했다",
    "install-failed": "설치기가 실패했다",
}
_WRAPPER_TOKEN: Final = re.compile(r"[a-z0-9.-]{1,63}\|(installed|skipped|failed)\|[A-Za-z0-9:=,.-]{1,120}\Z")


def _wrapper_refresh(entries: tuple[str, ...]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for entry in entries:
        if _WRAPPER_TOKEN.fullmatch(entry) is None:
            raise ObservationError(f"malformed wrapper refresh result: {entry[:80]!r}")
        node, status, token = entry.split("|")
        records.append({"node": node, "status": status, "detail": token})
    return records


def render_receipt(
    plan: Plan,
    *,
    verified_at: str,
    declared_reasons: Mapping[tuple[str, str, str], str] | None = None,
    wrapper_refresh: tuple[str, ...] = (),
    rag_mcp_refresh: str = "",
) -> str:
    """전량 반영 영수증 — clean 이 아닌 계획에 서명하는 것을 코드가 거부한다.

    `declared_reasons` 는 (kind, account, destination) → 선언의 `reason`. 보류 항목은 roster 보류가
    아니면 그 값을 사유로 적고, 없으면 관측 토큰으로 되돌아간다.
    """
    declared = declared_reasons or {}
    if not plan.clean:
        raise ObservationError("refusing to attest a non-clean deployment")
    retired = [a for a in plan.artifacts if a.status in ("retired-present", "retired-absent")]
    held = [a for a in plan.artifacts if a.status == "held"]
    refreshed = _wrapper_refresh(wrapper_refresh)
    payload: dict[str, object] = {
        "version": RECEIPT_VERSION,
        "release_sha": plan.release_sha,
        "verified_at": verified_at,
        "surfaces": {
            "skill_mounts": "ok",
            "home_artifacts": {
                "ok": sum(1 for s in plan.home if s.status == "ok"),
                "ok_absent_optional": sum(1 for s in plan.home if s.status == "ok-absent"),
            },
            "artifacts": {
                kind: {
                    "ok": sum(1 for a in plan.artifacts if a.kind == kind and a.status == "ok"),
                    "held": sum(1 for a in held if a.kind == kind),
                    "retired": sum(1 for a in retired if a.kind == kind),
                }
                for kind in KINDS
            },
        },
        "delegated": list(DELEGATED_SURFACES),
        "held": [
            {"account": a.account, "destination": a.destination, "reason": _held_reason(a, declared)}
            for a in held
        ] + receipt_held(plan.gateways),
        "retired": [
            {"account": a.account, "destination": a.destination, "present": a.status == "retired-present"}
            for a in retired
        ],
        "pending_owner_actions": [
            {
                "probe": check.probe,
                "node": check.node,
                "check": check.name,
                "status": check.status,
                "guidance": list(check.details),
            }
            for check in plan.pending
        ],
        "delegated_checked": plan.delegated_checked,
        "pending_unknown": plan.pending_unknown,
        "judged_at_release_only": list(JUDGED_AT_RELEASE_ONLY),
        "undeclared": [
            {
                "account": state.account,
                "destination": state.destination,
                "sha256_prefix": state.sha256_prefix,
            }
            for state in plan.undeclared
        ],
    }
    if refreshed:
        payload["wrapper_refresh"] = refreshed
    if rag_mcp_refresh:
        rag = parse_refresh(rag_mcp_refresh)
        payload["rag_mcp_refresh"] = rag
        if rag["status"] in ("failed", "skipped"):
            # Even if the source/image probe now passes, a failed smoke or rollback
            # is a failed release effect. It must remain visible to the owner.
            payload["pending_owner_actions"] = [
                *[
                    {"probe": check.probe, "node": check.node, "check": check.name,
                     "status": check.status, "guidance": list(check.details)}
                    for check in plan.pending if check.probe != "rag_stack_current"
                ],
                {"probe": "rag_stack_current", "node": "rag", "check": "RAG MCP 갱신",
                 "status": "FAIL", "guidance": [f"검색 갱신 실패/보류: {rag['detail']}"]},
            ]
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _flat(value: object) -> str:
    raw = value if isinstance(value, str) else str(value)
    flat = "".join(
        " " if ord(char) < 32 or ord(char) == 127 or char in "\u2028\u2029\u0085" else char
        for char in raw
    )
    return _MENTION.sub("@\u200b", flat)


def one_line(value: object) -> str:
    """노드에서 온 문자열을 한 줄의 데이터로 만든다 — 줄을 더하거나 멘션을 걸지 못한다."""
    return _flat(value).strip()[:_MAX_TEXT]


def notice_line(value: object) -> str:
    """이미 조립된 조치 줄을 통지에 실을 때 — 들여쓰기와 길이는 두고 줄·멘션만 막는다."""
    return _flat(value).rstrip()


def _records(document: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    value = document.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _action_lines(action: Mapping[str, object]) -> list[str]:
    where = f"{one_line(action.get('check', '?'))} ({one_line(action.get('probe', '?'))} · {one_line(action.get('node', '?'))})"
    status = action.get("status")
    head = f"확인 불가 — {where}" if status != "FAIL" else f"조치 필요 — {where}"
    guidance = action.get("guidance")
    lines = [f"- {head}"]
    if isinstance(guidance, list):
        lines += [f"  · {one_line(line)}" for line in guidance if isinstance(line, str) and line.strip()]
    return lines


def _held_lines(document: Mapping[str, object]) -> list[str]:
    counts: dict[str, int] = {}
    for item in _records(document, "held"):
        reason = item.get("reason")
        key = reason if isinstance(reason, str) and reason else "held"
        counts[key] = counts.get(key, 0) + 1
    lines: list[str] = []
    for reason, count in counts.items():
        if reason == "roster-required":
            said = _ROSTER_GUIDANCE
        elif reason == "roster-unverified":
            said = _ROSTER_UNVERIFIED
        elif reason == "roster-sibling":
            said = _ROSTER_SIBLING
        else:
            said = one_line(reason)
        lines.append(f"- 보류 {count}건 — {said}")
    return lines


def _wrapper_lines(document: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    for item in _records(document, "wrapper_refresh"):
        node = one_line(item.get("node", "?"))
        detail = str(item.get("detail", ""))
        if detail.startswith("mismatch:"):
            said = f"설치 뒤 지문 불일치({one_line(detail.removeprefix('mismatch:'))})"
        else:
            said = _WRAPPER_REASONS.get(detail, one_line(detail))
        match item.get("status"):
            case "failed":
                lines.append(f"- 헬스체크 허용 목록 갱신 실패 — {node}: {said}")
            case "skipped":
                lines.append(f"- 헬스체크 허용 목록 갱신 보류 — {node}: {said}")
            case _:
                pass
    return lines


def pending_lines(document: object) -> tuple[str, ...]:
    """영수증 JSON → 사람이 읽을 남은 소유자 조치 줄. v1·판독 불가 문서는 빈 목록이다(예외 없음)."""
    if not isinstance(document, Mapping):
        return ()
    version = document.get("version")
    if type(version) is not int or version < RECEIPT_VERSION:
        return ()
    lines: list[str] = _wrapper_lines(document)
    for action in _records(document, "pending_owner_actions"):
        lines += _action_lines(action)
    if document.get("pending_unknown") is not False:
        lines.append("- 확인 불가 — 위임 프로브(root 자산·RAG·헬스체크 허용 목록)를 릴리스 시점에 돌리지 못했다")
    lines += _held_lines(document)
    return ("남은 소유자 조치:", *lines) if lines else ()

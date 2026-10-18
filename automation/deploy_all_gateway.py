"""게이트웨이 세대 — 게이트 플러그인을 현재 릴리스에서 등록했는가를 관측하고 판정한다.

파일을 놓는 것만으로는 반영이 아니다: 게이트웨이는 시작할 때 플러그인을 로드하므로, 다시 떠서
현재 릴리스 세대에서 게이트를 등록했다는 기록(`automation.gateway_generation`)까지가 반영이다.
드롭인(`…/20-interop-guard.conf`)을 선언한 계정마다 노드가 `OBS|gateway|<계정>|<status>|<want>|
<have>|<detail>` 한 줄을 낸다 — 드롭인 행이 held 면 held(roster 를 놓아야 풀린다), ok 면 그 계정에서
돌린 세대 확인의 결과, 그 밖이면 줄이 없다(그 행 자체가 이미 결함이다). 노드에서 온 문자열은
데이터다: 토큰 모양이 아니거나 종료코드와 어긋나거나 다른 세대를 말하면 unknown 이다.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.deploy_all_kinds import ArtifactState, ObservationError
from automation.deploy_declarations import Declaration

DROPIN: Final = ".config/systemd/user/hermes-gateway.service.d/20-interop-guard.conf"
#: 보류 사유는 관측기가 그 계정의 artifact 행에 준 것과 같다 — init-local 은 roster-required 계정에만 맞는 안내다.
HELD_REASONS: Final = frozenset({"roster-required", "roster-unverified", "roster-sibling"})
STATUSES: Final = ("ok", "stale", "unknown", "held")
_PLUGIN: Final = re.compile(r"^\.hermes/plugins/([A-Za-z0-9_.-]+)/__init__\.py$")
_TOKEN: Final = re.compile(r"^[A-Za-z0-9_.:+-]+$")
_ACCOUNT: Final = re.compile(r"^[A-Za-z0-9_./-]+$")
_CHECK: Final = re.compile(r"^GATEWAY-GENERATION (ok|stale|unknown) want=(\S+) have=(\S+) detail=(\S+)$")
_RC_STATUS: Final = {0: "ok", 1: "stale", 2: "unknown"}

Runner = Callable[[str, tuple[str, ...]], tuple[int, str]]


@dataclass(frozen=True, slots=True)
class GatewayState:
    account: str
    status: str  # ok | stale | unknown | held
    want: str
    have: str
    detail: str


def bundle(declarations: Sequence[Declaration]) -> list[Declaration]:
    """드롭인을 선언한 묶음의 행 전부 — 게이트웨이만 다시 볼 때도 roster 보류가 전량 관측과 같게."""
    owners = {d.owner for d in declarations if d.destination == DROPIN}
    return [d for d in declarations if d.owner in owners]


def _check(account: str, runtime_root: Path, required: Sequence[str], call: Runner) -> str:
    want = runtime_root.resolve().name
    rc, out = call(account, ("gateway", str(runtime_root), *(a for n in required for a in ("--require", n))))
    match = _CHECK.fullmatch(out.strip())
    if match is None or _RC_STATUS.get(rc) != match[1] or not all(_TOKEN.fullmatch(v) for v in match.groups()):
        return f"OBS|gateway|{account}|unknown|{want}|-|unreadable-check"
    status, said_want, have, detail = match.groups()
    if said_want != want or (status == "ok" and have != want):
        return f"OBS|gateway|{account}|unknown|{want}|{have}|inconsistent-check"
    return f"OBS|gateway|{account}|{status}|{want}|{have}|{detail}"


def gateway_lines(
    declarations: Sequence[Declaration],
    statuses: Mapping[tuple[str, str], tuple[str, Sequence[tuple[str, str]]]],
    runtime_root: Path,
    call: Runner,
    services: Sequence[GatewayState] = (),
    *,
    hermes_gateways: str = "running",
) -> list[str]:
    if hermes_gateways == "stopped":
        return []
    inactive = {s.account for s in services if s.status != "ok"}
    lines: list[str] = []
    for decl in declarations:
        if decl.account in inactive:
            continue
        status, details = statuses.get((decl.account, decl.destination), ("", ())) if decl.destination == DROPIN else ("", ())
        if status == "held":
            lines.append(f"OBS|gateway|{decl.account}|held|-|-|{details[0][0] if details else '-'}")
        elif status == "ok":
            plugins = (_PLUGIN.fullmatch(d.destination) for d in declarations
                       if d.account == decl.account and d.attr("activation") == "gateway")
            lines.append(_check(decl.account, runtime_root, sorted({m[1] for m in plugins if m}), call))
    return lines


def _state(parts: list[str]) -> GatewayState:
    if len(parts) != 7:
        raise ObservationError(f"malformed gateway observation: {'|'.join(parts)[:120]}")
    state = GatewayState(*parts[2:])
    valid = (
        _ACCOUNT.fullmatch(state.account) is not None
        and state.status in STATUSES
        and all(_TOKEN.fullmatch(v) for v in (state.want, state.have, state.detail))
        and (state.status != "ok" or (state.want == state.have and state.detail == "-"))
        and (state.status != "held" or ((state.want, state.have) == ("-", "-") and state.detail in HELD_REASONS))
    )
    if not valid:
        raise ObservationError(f"malformed gateway observation: {'|'.join(parts)[:120]}")
    return state


def fold(
    rows: Iterable[list[str]], artifacts: Sequence[ArtifactState] | None,
    services: Sequence[GatewayState] = (), *, hermes_gateways: str = "running",
) -> tuple[GatewayState, ...]:
    """`OBS|gateway|…` 를 접는다. artifacts 를 주면 드롭인 행과 정확히 맞물려야 한다 —
    ok 인 드롭인에 줄이 없는 관측은 게이트웨이를 보지 않은 것이지 깨끗한 것이 아니다."""
    states: dict[str, GatewayState] = {}
    for parts in rows:
        state = _state(parts)
        if state.account in states:
            raise ObservationError(f"duplicate gateway observation: {state.account}")
        states[state.account] = state
    if artifacts is not None:
        dropins = {a.account: a.status for a in artifacts if a.kind == "file" and a.destination == DROPIN}
        inactive = {s.account for s in services if s.status != "ok"}
        expected = {account: status == "held" for account, status in dropins.items()
                    if status in ("ok", "held") and account not in inactive and hermes_gateways != "stopped"}
        if {account: s.status == "held" for account, s in states.items()} != expected:
            raise ObservationError("gateway observations do not match the drop-in rows")
    return tuple(states.values())


def blocking(states: Iterable[GatewayState]) -> bool:
    return any(s.status in ("stale", "unknown") for s in states)


def restart_needed(states: Iterable[GatewayState]) -> bool:
    return any(s.status == "stale" for s in states)


def render_lines(states: Iterable[GatewayState]) -> list[str]:
    lines: list[str] = []
    for s in states:
        if s.status == "stale":
            lines.append(f"  GATEWAY-STALE {s.account}: 릴리스 {s.want} / 등록 {s.have} ({s.detail}) → agent+peer 재시동")
        elif s.status == "unknown":
            lines.append(f"  GATEWAY-UNKNOWN {s.account}: 세대를 확인하지 못함({s.detail}, fail-closed)")
        elif s.status == "held":
            lines.append(f"  GATEWAY-HELD {s.account} ({s.detail}, 게이트를 막지 않음)")
    return lines


def actions(states: Iterable[GatewayState]) -> list[str]:
    """unknown 은 재시동으로 고칠 수 있는지 모른다 — 재시동하지 않고 사람에게 넘긴다."""
    return [f"ACT|manual|gateway-unknown:{s.account}" for s in states if s.status == "unknown"]


def receipt_held(states: Iterable[GatewayState]) -> list[dict[str, str]]:
    return [{"account": s.account, "destination": "gateway-generation", "reason": s.detail}
            for s in states if s.status == "held"]


def service_lines(accounts: Sequence[str], wanted: str, call: Runner) -> list[str]:
    """Observe systemd separately from plugin generation, including the release-owned marker."""
    lines: list[str] = []
    for account in accounts:
        rc, out = call(account, ("gateway-service",))
        fields = out.strip().splitlines()
        have, detail = "-", "unreadable-service"
        if len(fields) == 2 and fields[1] in ("marked", "unmarked"):
            if rc == 0 and fields[0] == "active":
                have = "running"
            elif rc == 3 and fields[0] == "inactive":
                have = "stopped"
            if have != "-":
                detail = ("marker-missing" if wanted == "stopped" and fields[1] != "marked"
                          else "marker-present" if wanted == "running" and fields[1] == "marked" else "-")
        status = "unknown" if have == "-" else "ok" if have == wanted and detail == "-" else "stale"
        lines.append(f"OBS|gateway-service|{account}|{status}|{wanted}|{have}|{detail}")
    return lines


def fold_services(
    rows: Iterable[list[str]], accounts: set[str], wanted: str,
) -> tuple[GatewayState, ...]:
    """A cut or inconsistent service observation must not attest a successful stop."""
    states = tuple(_state(parts) for parts in rows)
    if (len(states) != len(accounts) or {s.account for s in states} != accounts
            or any(s.want != wanted or s.have not in ("running", "stopped", "-")
                   or s.status not in ("ok", "stale", "unknown")
                   or (s.status == "unknown") != (s.have == "-")
                   or (s.status == "stale" and s.have == wanted and s.detail not in ("marker-missing", "marker-present"))
                   or s.detail not in ("-", "marker-missing", "marker-present", "unreadable-service") for s in states)):
        raise ObservationError("gateway service observations do not match the requested mode")
    return states


def service_actions(states: Sequence[GatewayState], wanted: str) -> list[str]:
    verb = "stop" if wanted == "stopped" else "start"
    change = [f"ACT|{verb}-gateway|agent+peer"] if any(s.status == "stale" for s in states) else []
    return change + [f"ACT|manual|gateway-service-unknown:{s.account}" for s in states if s.status == "unknown"]

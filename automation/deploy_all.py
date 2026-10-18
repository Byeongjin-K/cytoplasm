"""배포 전량 판정의 순수 로직 (RC-3/4) — 관측을 받아 계획·행동·영수증을 만든다.

"origin/main 의 모든 것이 prod 에 있는가"는 지금까지 표면별 프로브 넷에 흩어져 있었고
한 번에 묻는 판정이 없었다 — 그래서 스킬 6개 중 2개만 배포된 상태와 전부 배포된 상태가
명령 수준에서 구분되지 않았다(계획 C3, 2026-08-01 실측 2일 침묵). 이 모듈은 노드 관측
(`deploy_all_probe`)을 받아 그 질문에 하나의 답을 낸다: 계획(무엇이 낡았나) ·
행동(어느 배포기를 부르나) · 영수증(전량 반영이 검증된 릴리스 sha).

I/O 가 없다 — 관측 줄을 받아 판정을 돌려줄 뿐이다. 관측은 노드에서
`automation/deploy_all_probe.py` 가, 실행은 `automation/deploy_all.sh` 가 소유한다.
영수증은 clean 일 때만 만들어진다 — 부분 성공을 성공으로 보고하지 않는 것이 이 모듈의
존재 이유다.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from automation import deploy_all_gateway, deploy_all_kinds
from automation.deploy_all_gateway import GatewayState
from automation.deploy_all_kinds import (
    ArtifactState,
    ObservationError,
    PendingCheck,
    defective,
    owner_actions,
    render_artifact_lines,
)
from automation.deploy_receipt import DELEGATED_SURFACES, RECEIPT_VERSION, render_receipt

__all__ = [
    "DELEGATED_SURFACES",
    "RECEIPT_VERSION",
    "ArtifactState",
    "GatewayState",
    "ObservationError",
    "PendingCheck",
    "render_receipt",
]


@dataclass(frozen=True, slots=True)
class HomeState:
    account: str
    destination: str
    source: str
    policy: str  # "required" | "optional"
    release_sha: str
    deployed_sha: str  # "" = 없음 · "?" = 읽지 못함

    @property
    def owning_package(self) -> str:
        return "/".join(self.source.split("/")[:2])

    @property
    def status(self) -> str:
        if self.deployed_sha == "?":
            return "unknown"
        if not self.deployed_sha:
            return "ok-absent" if self.policy == "optional" else "absent"
        return "ok" if self.deployed_sha == self.release_sha else "stale"


@dataclass(frozen=True, slots=True)
class UndeclaredState:
    """배포기가 소유하지 않으므로 기본 판정은 경고만 하는 노드 홈 파일이다."""

    account: str
    destination: str
    sha256_prefix: str


@dataclass(frozen=True, slots=True)
class Plan:
    release_sha: str
    mount_stale: tuple[tuple[str, str, str], ...]
    mount_unmounted: tuple[str, ...]
    mount_orphaned: tuple[str, ...]
    home: tuple[HomeState, ...]
    undeclared: tuple[UndeclaredState, ...]
    strict_undeclared: bool = False
    artifacts: tuple[ArtifactState, ...] = ()
    pending: tuple[PendingCheck, ...] = ()
    pending_unknown: bool = False
    delegated_checked: int = 0
    gateways: tuple[GatewayState, ...] = ()
    hermes_gateways: str = "running"
    gateway_services: tuple[GatewayState, ...] = ()

    @property
    def home_defects(self) -> tuple[HomeState, ...]:
        return tuple(s for s in self.home if s.status in ("stale", "absent", "unknown"))

    @property
    def skills_to_deploy(self) -> tuple[str, ...]:
        names = {name for name, _, _ in self.mount_stale} | set(self.mount_unmounted)
        return tuple(sorted(names))

    @property
    def deployable_artifacts(self) -> tuple[ArtifactState, ...]:
        return tuple(a for a in self.artifacts if a.status in deploy_all_kinds.DEPLOYABLE)

    @property
    def packages_to_deploy(self) -> tuple[str, ...]:
        home = {s.owning_package for s in self.home_defects if s.status in ("stale", "absent")}
        return tuple(sorted(home | {a.owner for a in self.deployable_artifacts}))

    @property
    def gateway_restart_needed(self) -> bool:
        """플러그인은 게이트웨이 프로세스 시작 시 로드된다 — 파일 배포만으로는 반영이 아니다."""
        if self.hermes_gateways == "stopped":
            return False
        return any(
            s.destination.startswith(".hermes/plugins/") for s in self.home_defects
        ) or any(a.activation == "gateway" for a in self.deployable_artifacts) or deploy_all_gateway.restart_needed(
            self.gateways
        )

    @property
    def clean(self) -> bool:
        defects = (
            self.mount_stale or self.mount_unmounted or self.mount_orphaned or self.home_defects
            or any(defective(a) for a in self.artifacts) or deploy_all_gateway.blocking(self.gateways)
            or deploy_all_gateway.blocking(self.gateway_services)
        )
        return not defects and not (self.strict_undeclared and self.undeclared)


def parse_observations(
    lines: Iterable[str], *, strict_undeclared: bool = False
) -> Plan:
    """관측 줄을 엄격하게 접는다. `OBS|end` 가 없으면 잘린 것이고, 잘린 것은 판정이 아니다."""
    release_sha = ""
    mounts_judged = False
    ended = False
    mount_stale: list[tuple[str, str, str]] = []
    mount_unmounted: list[str] = []
    mount_orphaned: list[str] = []
    home: list[HomeState] = []
    undeclared: list[UndeclaredState] = []
    family: list[str] = []
    gateway_rows: list[list[str]] = []
    service_rows: list[list[str]] = []
    gateway_mode: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line.startswith("OBS|"):
            continue  # ssh 전송에 섞인 비관측 줄은 판정에 쓰지 않는다
        parts = line.split("|")
        kind = parts[1]
        if kind == "release" and len(parts) == 3 and parts[2]:
            release_sha = parts[2]
        elif kind == "mounts" and len(parts) == 3 and parts[2] == "judged":
            mounts_judged = True
        elif kind == "mount-stale" and len(parts) == 5:
            mount_stale.append((parts[2], parts[3], parts[4]))
        elif kind == "mount-unmounted" and len(parts) == 3:
            mount_unmounted.append(parts[2])
        elif kind == "mount-orphaned" and len(parts) == 3:
            mount_orphaned.append(parts[2])
        elif kind == "home" and len(parts) == 8:
            home.append(HomeState(parts[2], parts[3], parts[4], parts[5], parts[6], parts[7]))
        elif kind == "undeclared" and len(parts) == 5 and parts[4]:
            undeclared.append(UndeclaredState(parts[2], parts[3], parts[4]))
        elif kind == "end" and len(parts) == 2:
            ended = True
        elif kind == "gateway":
            gateway_rows.append(parts)
        elif kind == "gateway-service":
            service_rows.append(parts)
        elif kind == "gateway-mode" and len(parts) == 3 and parts[2] in ("running", "stopped") and gateway_mode is None:
            gateway_mode = parts[2]
        elif kind in deploy_all_kinds.FAMILY:
            family.append(line)
        else:
            raise ObservationError(f"malformed observation: {line[:80]}")
    if not ended:
        raise ObservationError("truncated observations: OBS|end is missing")
    if not release_sha:
        raise ObservationError("no release sha observed")
    if not mounts_judged:
        raise ObservationError("mounts were not judged")
    if not home:
        raise ObservationError("no home-artifact observations")
    kinds = deploy_all_kinds.parse_artifact_lines(family)
    accounts = {a.account for a in kinds.artifacts if a.destination == deploy_all_gateway.DROPIN}
    if gateway_mode == "stopped":
        accounts |= {"agent", "peer"}
    services = deploy_all_gateway.fold_services(service_rows, accounts, gateway_mode or "running") if gateway_mode is not None else ()
    if service_rows and gateway_mode is None:
        raise ObservationError("gateway service observations require a mode")
    return Plan(
        release_sha=release_sha,
        mount_stale=tuple(mount_stale),
        mount_unmounted=tuple(mount_unmounted),
        mount_orphaned=tuple(mount_orphaned),
        home=tuple(home),
        undeclared=tuple(undeclared),
        strict_undeclared=strict_undeclared,
        artifacts=kinds.artifacts,
        pending=kinds.pending,
        pending_unknown=kinds.pending_unknown,
        delegated_checked=kinds.delegated_checked,
        gateways=deploy_all_gateway.fold(gateway_rows, kinds.artifacts, services, hermes_gateways=gateway_mode or "running"),
        hermes_gateways=gateway_mode or "running",
        gateway_services=services,
    )


def render_plan(plan: Plan) -> str:
    header = f"DEPLOY-ALL: release {plan.release_sha[:16]}"
    services = [f"  GATEWAY-SERVICE {state.account}: {state.status} want={state.want} have={state.have} ({state.detail})"
                for state in plan.gateway_services]
    if plan.clean and not plan.undeclared:
        return "\n".join([f"{header} — 전량 일치, 할 일 없음", *render_artifact_lines(plan),
                          *deploy_all_gateway.render_lines(plan.gateways), *services])
    lines = [header]
    for skill, expected, mounted in plan.mount_stale:
        lines.append(
            f"  SKILL-STALE {skill}: 릴리스 {expected[:12]}… / 마운트 {mounted[:12]}…"
            f" → deploy-skill.sh {skill}"
        )
    for skill in plan.mount_unmounted:
        lines.append(f"  SKILL-UNMOUNTED {skill} → deploy-skill.sh {skill}")
    for skill in plan.mount_orphaned:
        lines.append(f"  SKILL-ORPHANED {skill} — 릴리스에 없음(수동 판단: --remove)")
    for state in plan.home_defects:
        if state.status == "unknown":
            lines.append(
                f"  HOME-UNKNOWN {state.account}:{state.destination} — 읽지 못함(fail-closed)"
            )
        else:
            lines.append(
                f"  HOME-{state.status.upper()} {state.account}:{state.destination}"
                f" → {state.owning_package}/deploy.sh"
            )
    if plan.undeclared:
        suffix = " (STRICT: drift)" if plan.strict_undeclared else ""
        lines.append(f"  UNDECLARED HOME WARNINGS:{suffix}")
        for state in plan.undeclared:
            lines.append(
                f"    {state.account}:{state.destination} sha256={state.sha256_prefix}…"
            )
    lines += render_artifact_lines(plan) + deploy_all_gateway.render_lines(plan.gateways)
    lines += services
    if plan.gateway_restart_needed:
        lines.append("  GATEWAY-RESTART 필요: 플러그인 갱신은 agent+peer 재시동까지가 반영이다")
    return "\n".join(lines)


def render_actions(plan: Plan) -> str:
    """오케스트레이터가 기계로 읽는 행동 목록. clean 이면 빈 문자열이다."""
    lines: list[str] = []
    for skill in plan.skills_to_deploy:
        lines.append(f"ACT|deploy-skill|{skill}")
    for package in plan.packages_to_deploy:
        lines.append(f"ACT|run-deployer|{package}/deploy.sh")
    if plan.gateway_restart_needed:
        lines.append("ACT|restart-gateway|agent+peer")
    lines += deploy_all_gateway.service_actions(plan.gateway_services, plan.hermes_gateways)
    for skill in plan.mount_orphaned:
        lines.append(f"ACT|manual|orphaned-mount:{skill}")
    for state in plan.home_defects:
        if state.status == "unknown":
            lines.append(f"ACT|manual|unreadable:{state.account}:{state.destination}")
    for art in plan.artifacts:
        if art.status == "unknown":
            lines.append(f"ACT|manual|unreadable:{art.kind}:{art.account}:{art.destination}")
    lines += deploy_all_gateway.actions(plan.gateways) + owner_actions(plan)
    return "\n".join(lines)


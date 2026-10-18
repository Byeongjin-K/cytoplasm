"""Release approval CLI over the shared skill gate and lifecycle (VA-1).

One owner ✅ binds one version, HEAD and surface set; the nonce is outside the hash.
Decision exits: 0 approved · 9 denied · 7 pending/unverifiable · 2 absent/other HEAD.
Opt-in completion discovery: 3 verified bound candidate (not approval of the tip).
"""
# 새 승인 기계장치는 하나도 만들지 않는다 — 스펙(`ReleaseSpec`)·게이트(`SkillApprovalGate`)·
# lifecycle 호스트(`skill_gate_request`)를 그대로 재사용하고, 표면은 `ApprovalKind.RELEASE`
# 가 선언한다(§10-2 — 2차 주체인 peer 봇이 같은 채널을 읽는다).
# action hash 는 무작위 nonce 를 제외한다. 그래서 같은 plan 으로 명령을 다시 돌리면 살아 있는
# 요청을 고아로 만들지 않고 **재개**한다(승인 메시지 단일성 규칙).
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Final

from automation import (
    release_abandon,
    release_notes,
    release_plan,
    skill_gate,
    skill_gate_request,
    skill_gate_surface,
)
from automation.interop.approval_lifecycle import (
    ApprovalRecordsError,
    ApprovalSurfaceError,
)
from automation.interop.approval_surface import ApprovalKind
from automation.release_card import preflight_new_request, spec_from_plan
from automation.release_completion_target import (
    DECISION_APPROVED as DECISION_APPROVED,
    DECISION_UNAVAILABLE as DECISION_UNAVAILABLE,
    DECISION_PENDING as DECISION_PENDING,
    DECISION_DENIED as DECISION_DENIED,
    decision_exit as decision_exit,
    approval_decision,
)
from .release_request_gate import ReleaseRequestGate
from automation.notice_bot import SURFACE as NOTICE_BOT_SURFACE
from automation.omo_relay_policy import SURFACE as OMO_RELAY_SURFACE
from automation.release_spec import (
    ReleaseSpec,
    ReleaseSpecError,
    RAG_MCP_SURFACE,
    spec_from_record,
    dori_surface,
    gateway_stop_surface,
    yoodori_channel_surface,
    wrapper_surface,
)
from automation.release_released import ReleasedTagsError, parse_released
from automation.release_retire import notify_stale_approval
from automation.skill_gate_approval import GateSurface, SkillApprovalGate

#: release.sh 의 자동 복구가 읽는 유일한 기계 판독 줄 — 거절 메시지는 바뀌지 않는다.
STALE_PENDING_PREFIX: Final = "RELEASE-REQUEST-STALE:"
_WRAPPER_NODE: Final = re.compile(r"[a-z0-9][a-z0-9.-]{0,62}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
def _bindings() -> skill_gate_surface.SupplyChainSurface:
    return skill_gate_surface.surface_for(
        ApprovalKind.RELEASE, skill_gate._identity()  # noqa: SLF001 - the gate owns this
    )


def _gate(spec: ReleaseSpec) -> SkillApprovalGate:
    surface = GateSurface(
        skill_gate._api,  # noqa: SLF001 - the gate owns this, deliberately
        skill_gate.GATE_DIR,
        skill_gate._owner_id,  # noqa: SLF001
        _bindings,
    )
    return SkillApprovalGate(surface, spec)


def _record_path() -> Path:
    return skill_gate.GATE_DIR / "pending" / "release.json"


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        plan = release_plan.build_plan(
            Path(args.repo),
            base=args.base,
            head=args.head,
            version=args.version,
            bump=args.bump,
        )
    except release_plan.ReleasePlanError as error:
        print(f"RELEASE-PLAN-BLOCK: {error}", file=sys.stderr)
        return 4
    surfaces = list(plan.surface_digests)
    if getattr(args, "ledger", None) == "on":
        from automation.ledger_policy import surface as ledger_surface
        surfaces.append(ledger_surface())
    relay_digest = getattr(args, "omo_relay", None)
    if relay_digest is not None:
        if _SHA256.fullmatch(relay_digest) is None:
            print("RELEASE-PLAN-BLOCK: invalid omo-relay digest", file=sys.stderr)
            return 4
        surfaces.append((OMO_RELAY_SURFACE, relay_digest))
    from automation.release_spec import legacy_service_surface as legacy_surface

    surfaces.extend(legacy_surface(name) for name in getattr(args, "legacy_service_off", None) or ())
    if getattr(args, "hermes_product", None) == "retired":
        from automation.hermes_product import surface as product_surface

        surfaces.append(product_surface())
    if getattr(args, "peer_retired", None) == "retired":
        from automation.peer_retired import surface

        if getattr(args, "hermes_gateways", None) != "stopped":
            print("RELEASE-PLAN-BLOCK: peer retirement requires stopped gateways", file=sys.stderr)
            return 4
        surfaces.append(surface())
    from automation.auto_write_policy import surface as auto_write_surface

    try:
        surfaces.extend(auto_write_surface(kind) for kind in getattr(args, "auto_write", None) or ())
    except ValueError as error:
        print(f"RELEASE-PLAN-BLOCK: {error}", file=sys.stderr)
        return 4
    notice_digest = getattr(args, "notice_bot", None)
    fault_channel = getattr(args, "fault_channel", None)
    if fault_channel is not None:
        from automation.notice_bot import fault_surface

        try:
            if notice_digest is None:
                raise ValueError("fault-channel requires notice-bot")
            surfaces.append(fault_surface(fault_channel))
        except ValueError as error:
            print(f"RELEASE-PLAN-BLOCK: {error}", file=sys.stderr)
            return 4
    if notice_digest is not None:
        if (_SHA256.fullmatch(notice_digest) is None
                or getattr(args, "hermes_gateways", None) != "stopped"
                or not getattr(args, "yoodori_channel", None)):
            print("RELEASE-PLAN-BLOCK: notice-bot requires digest, stopped gateways and channel", file=sys.stderr)
            return 4
        surfaces.append((NOTICE_BOT_SURFACE, notice_digest))
    if getattr(args, "hermes_gateways", None) == "stopped":
        surfaces.append(gateway_stop_surface())
    channel_id = getattr(args, "yoodori_channel", None)
    if channel_id is not None:
        try:
            surfaces.append(yoodori_channel_surface(channel_id))
        except ReleaseSpecError as error:
            print(f"RELEASE-PLAN-BLOCK: {error}", file=sys.stderr)
            return 4
    bot_id = getattr(args, "dori_agent_chat", None)
    if bot_id is not None:
        try:
            surfaces.append(dori_surface(bot_id))
        except ReleaseSpecError as error:
            print(f"RELEASE-PLAN-BLOCK: {error}", file=sys.stderr)
            return 4
    for item in args.healthcheck_wrapper or ():
        node, _, digest = item.partition("=")
        if _WRAPPER_NODE.fullmatch(node) is None or _SHA256.fullmatch(digest) is None:
            print(f"RELEASE-PLAN-BLOCK: bad --healthcheck-wrapper {item!r}", file=sys.stderr)
            return 4
        surfaces.append((wrapper_surface(node), digest))
    rag_digest = getattr(args, "rag_mcp", None)
    if rag_digest:
        if _SHA256.fullmatch(rag_digest) is None:
            print("RELEASE-PLAN-BLOCK: bad --rag-mcp digest", file=sys.stderr)
            return 4
        surfaces.append((RAG_MCP_SURFACE, rag_digest))
    notes_dir = Path(os.environ.get("RELEASE_NOTES_DIR") or Path.home() / ".hermes/release-notes")
    previous = release_notes.previous_surfaces(notes_dir, plan.base)
    payload = {
        "card_summary": release_notes.card_summary(notes_dir, plan.head),
        **({"previous_surfaces": previous} if previous is not None else {}),
        "base": plan.base,
        "head": plan.head,
        "major_note": (release_notes.major_note(plan, p5_retired=True)
                       if getattr(args, "peer_retired", None) == "retired"
                       else release_notes.major_note(plan)),
        "patch_notes": release_notes.detail_body(plan),
        "surface_digests": [list(row) for row in sorted(surfaces)],
        "version": plan.version,
    }
    try:
        release_notes.save_detail(
            notes_dir, plan.head, tuple(sorted(surfaces)), release_notes.detail_body(plan),
        )
        release_notes.save_surfaces(notes_dir, plan.head, tuple(sorted(surfaces)))
    except OSError as error:
        print(f"RELEASE-PLAN-BLOCK: cannot save release detail: {type(error).__name__}", file=sys.stderr)
        return 4
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_retire(args: argparse.Namespace) -> int:
    try:
        released = parse_released(str(getattr(args, "released", "")))
    except ReleasedTagsError as error:
        print(error, file=sys.stderr)
        return 4
    return release_abandon.retire_previous(
        str(args.head), str(getattr(args, "tip", "")), _gate, released,
    )


def _stale_pending_line() -> str | None:
    """Blocking record identity with its own verified probe; uncertainty yields no hint."""
    # `binding-mismatch` 거절은 '다른 요청이 살아 있다'까지만 말한다. 그 요청이 소유자 결정을
    # 기다리는 중인지, 어떤 버전·HEAD 에 묶여 있는지는 레코드 자신의 스펙으로 다시 프로브해야만
    # 알 수 있다(`cmd_decision` 과 같은 복원 경로, 사본 0). 확인에 실패하면 아무 줄도 내지
    # 않는다 — 불확실은 자동 복구의 근거가 될 수 없다.
    try:
        decoded = json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    record = {str(name): str(value) for name, value in decoded.items()}
    try:
        gate = _gate(spec_from_record(record))
        outstanding = gate.outstanding("release")
        if not outstanding:
            return None
        probe = gate.probe(outstanding[0])
    except (ReleaseSpecError, ApprovalRecordsError, ApprovalSurfaceError, OSError):
        return None
    return (
        f"{STALE_PENDING_PREFIX} version={record.get('version', '')}"
        f" head={record.get('head_sha', '')}"
        f" message_id={record.get('message_id', '')}"
        f" probe={probe.name.lower()}"
    )


def _emit_request(requested: skill_gate_request.Requested) -> int:
    """The legacy stdout/exit contract byte-for-byte, plus ONE stale hint on stderr."""
    exit_code = skill_gate_request.emit(requested, json_output=True)
    if exit_code == 0 or "reason=binding-mismatch" not in requested.message:
        return exit_code
    stale = _stale_pending_line()
    if stale is not None:
        print(stale, file=sys.stderr)
    return exit_code


def cmd_request(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReleaseSpecError("plan file is not a JSON object")
    spec = spec_from_plan(payload, secrets.token_hex(16))
    # 살아 있는 바인딩부터 해소한다 — 소유자가 이미 보고 있는 카드는 그 바이트에 결정이
    # 묶여 있으므로, 재사용 경로는 렌더러를 부르지 않는다(다시 그린 한 글자가 곧 거절이다).
    reused = skill_gate_request.reuse(_gate(spec))
    if reused is not None:
        return _emit_request(reused)
    # 재사용할 것이 없을 때에만 판본과 최악 링크 길이 예산을 확정한다. 실제 상세 좌표를
    # 얻은 최종 본문은 저널 뒤 release 전용 post 경계가 고정하되 같은 예산을 넘을 수 없다.
    candidate, refusal = preflight_new_request(spec)
    if candidate is None:
        refused = skill_gate_request.Requested(None, skill_gate_request.LIFECYCLE_REFUSAL_EXIT, refusal)
        return _emit_request(refused)
    gate = ReleaseRequestGate(
        _gate(candidate),
        skill_gate._api,  # noqa: SLF001 - release uses the gate's existing API seam
    )
    print(skill_gate_surface.where_to_look(ApprovalKind.RELEASE), file=sys.stderr)
    requested = skill_gate_request.post_request(gate, fresh=False)
    return _emit_request(requested)


def cmd_abandon(args: argparse.Namespace) -> int:
    """Delegate audited abandonment through the existing remote producer surface."""
    return release_abandon.command(args)


def cmd_decision(args: argparse.Namespace) -> int:
    return approval_decision(args, _gate, notify_stale_approval)


def cmd_authorize_wrapper(args: argparse.Namespace) -> int:
    """Did the owner's ✅ on release ``--head`` cover this exact wrapper fingerprint?

    Same judgement as a skill riding the release approval (``release-authorize``): the
    surface must be in the approved record's hash preimage, and the record must be the
    release now deployed. Exit 0 authorized · 4 not covered/other release · 8 ⛔ ·
    1 pending/unverifiable · 2 no usable record.
    """
    return skill_gate.cmd_release_authorize(
        argparse.Namespace(
            skill=None,
            surface=wrapper_surface(args.node),
            hash=args.hash,
            head=args.head,
            peer_attest_mode="",
        )
    )


def cmd_authorize_dori(args: argparse.Namespace) -> int:
    try:
        surface, digest = dori_surface(args.bot_id)
    except ReleaseSpecError as error:
        print(error, file=sys.stderr)
        return 4
    return skill_gate.cmd_release_authorize(
        argparse.Namespace(skill=None, surface=surface, hash=digest,
                           head=args.head, peer_attest_mode="")
    )


def cmd_authorize_gateway_stop(args: argparse.Namespace) -> int:
    surface, digest = gateway_stop_surface()
    return skill_gate.cmd_release_authorize(
        argparse.Namespace(skill=None, surface=surface, hash=digest,
                           head=args.head, peer_attest_mode="")
    )

def cmd_authorize_legacy_service(args: argparse.Namespace) -> int:
    from automation.release_spec import legacy_service_surface

    name, digest = legacy_service_surface(args.name)
    return skill_gate.cmd_release_authorize(
        argparse.Namespace(skill=None, surface=name, hash=digest, head=args.head, peer_attest_mode="")
    )


def cmd_authorize_peer_retired(args: argparse.Namespace) -> int:
    from automation.peer_retired import surface

    name, digest = surface()
    return skill_gate.cmd_release_authorize(
        argparse.Namespace(skill=None, surface=name, hash=digest,
                           head=args.head, peer_attest_mode="")
    )


def cmd_authorize_hermes_product(args: argparse.Namespace) -> int:
    from automation.hermes_product import surface

    name, digest = surface()
    return skill_gate.cmd_release_authorize(
        argparse.Namespace(skill=None, surface=name, hash=digest,
                           head=args.head, peer_attest_mode="")
    )


def cmd_authorize_yoodori_channel(args: argparse.Namespace) -> int:
    try:
        surface, digest = yoodori_channel_surface(args.channel_id)
    except ReleaseSpecError as error:
        print(error, file=sys.stderr)
        return 4
    return skill_gate.cmd_release_authorize(
        argparse.Namespace(skill=None, surface=surface, hash=digest,
                           head=args.head, peer_attest_mode="")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-approval")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="표면별 변경·패치노트 계획을 JSON 으로 출력")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--base", required=True)
    plan.add_argument("--head", required=True)
    plan.add_argument("--version", required=True)
    plan.add_argument("--bump", choices=("major", "minor", "patch"), default="patch")
    plan.add_argument("--dori-agent-chat", metavar="BOT_ID", help="릴리스 호스트의 명시적 도리 전환")
    plan.add_argument("--hermes-gateways", choices=("stopped",), help="릴리스 호스트의 명시적 게이트웨이 정지")
    plan.add_argument("--peer-retired", choices=("retired",), help="백업 뒤 P5 비루트 제거")
    plan.add_argument("--hermes-product", choices=("retired",), help="설정·장부 원본 백업 뒤 Hermes 제품만 제거")
    plan.add_argument("--yoodori-channel", metavar="CHANNEL_ID", help="릴리스가 선택한 대화·알림 채널")
    plan.add_argument("--notice-bot", metavar="DIGEST", help="발신 자격증명 SHA-256만 승인에 바인딩")
    plan.add_argument("--fault-channel", metavar="CHANNEL_ID", help="고장 알림을 유돌·리드 페어 채널로")
    plan.add_argument("--auto-write", action="append", help="카드 없이 쓰는 생산자 종류")
    plan.add_argument("--ledger", choices=("on",), help="Hermes 카드를 검증 후 작은 장부로 이관")
    plan.add_argument("--omo-relay", metavar="DIGEST", help="이 PC omo 중계 설정 지문")
    plan.add_argument("--legacy-service-off", action="append", choices=("report-hub", "hermes-dashboard", "hermes-memory"))
    plan.add_argument(
        "--healthcheck-wrapper",
        action="append",
        metavar="NODE=DIGEST",
        help="새 릴리스가 기대하는 래퍼 지문이 설치본과 다른 노드 — 승인 표면으로 묶는다",
    )
    plan.set_defaults(run=cmd_plan)
    plan.add_argument("--rag-mcp", metavar="DIGEST", help="stale RAG source/image: bind the MCP-only rebuild")
    retire = commands.add_parser(
        "retire", help="서명·승인 완료된 이전 release를 감사 archive로 이동"
    )
    retire.add_argument("--head", required=True)
    retire.add_argument("--tip", default="", help="확인한 origin/main tip; 낡은 승인 자가 회수")
    retire.add_argument("--released", default="",
                        help="서명 릴리스 태그 v=sha,… — 태그가 가리키는 레코드는 반응과 무관하게 실행 완료")
    retire.set_defaults(run=cmd_retire)
    request = commands.add_parser("request", help="release 승인 요청 게시(재실행=재사용)")
    request.add_argument("--plan-file", required=True)
    request.set_defaults(run=cmd_request)
    abandon = commands.add_parser(
        "abandon", help="막고 있는 release 레코드를 감사와 함께 archive 로 놓아준다"
    )
    abandon.add_argument("--version", required=True)
    abandon.add_argument("--head", required=True)
    abandon.add_argument("--message-id", required=True)
    abandon.add_argument("--reason", required=True)
    abandon.set_defaults(run=cmd_abandon)
    decision = commands.add_parser("decision", help="소유자 결정 조회(0/9/7/2)")
    decision.add_argument("--head", default="")
    decision.add_argument("--notify-stale", action="store_true")
    decision.add_argument("--completion-candidate", action="store_true",
                          help="다른 HEAD 의 승인 후보만 출력(rc 3); 조상 판정은 워크스테이션이 수행")
    decision.add_argument(
        "--tagged",
        default="",
        help="origin/main 에서 도달 가능한 가장 가까운 릴리스 커밋 — 완결기가 실어 준다",
    )
    decision.set_defaults(run=cmd_decision)
    authorize = commands.add_parser(
        "authorize-wrapper", help="릴리스 ✅ 가 이 노드의 허용 목록 래퍼 지문을 덮는가(0/4/8/1/2)"
    )
    authorize.add_argument("--node", required=True)
    authorize.add_argument("--hash", required=True)
    authorize.add_argument("--head", required=True)
    authorize.set_defaults(run=cmd_authorize_wrapper)
    dori = commands.add_parser("authorize-dori-agent-chat", help="이 릴리스 ✅ 가 도리 전환을 덮는가")
    dori.add_argument("--bot-id", required=True)
    dori.add_argument("--head", required=True)
    dori.set_defaults(run=cmd_authorize_dori)
    stop = commands.add_parser("authorize-hermes-gateways-stopped", help="이 릴리스 ✅ 가 양 게이트웨이 정지를 덮는가")
    stop.add_argument("--head", required=True)
    stop.set_defaults(run=cmd_authorize_gateway_stop)
    legacy = commands.add_parser("authorize-legacy-service")
    legacy.add_argument("--name", choices=("report-hub", "hermes-dashboard", "hermes-memory"), required=True)
    legacy.add_argument("--head", required=True)
    legacy.set_defaults(run=cmd_authorize_legacy_service)
    retired = commands.add_parser("authorize-peer-retired")
    retired.add_argument("--head", required=True)
    retired.set_defaults(run=cmd_authorize_peer_retired)
    product = commands.add_parser("authorize-hermes-product")
    product.add_argument("--head", required=True)
    product.set_defaults(run=cmd_authorize_hermes_product)
    channel = commands.add_parser("authorize-yoodori-channel", help="이 릴리스 ✅ 가 채널 합치기를 덮는가")
    channel.add_argument("--channel-id", required=True)
    channel.add_argument("--head", required=True)
    channel.set_defaults(run=cmd_authorize_yoodori_channel)
    fault = commands.add_parser("authorize-fault-channel")
    fault.add_argument("--channel-id", required=True)
    fault.add_argument("--head", required=True)
    def authorize_fault(args: argparse.Namespace) -> int:
        from automation.notice_bot import fault_surface

        try:
            name, digest = fault_surface(args.channel_id)
        except ValueError:
            return 4
        return skill_gate.cmd_release_authorize(argparse.Namespace(
            skill=None, surface=name, hash=digest, head=args.head, peer_attest_mode="",
        ))
    fault.set_defaults(run=authorize_fault)
    rag = commands.add_parser("authorize-rag-mcp", help="authorize the release-bound MCP-only rebuild")
    rag.add_argument("--hash", required=True)
    rag.add_argument("--head", required=True)
    rag.set_defaults(run=lambda args: skill_gate.cmd_release_authorize(
        argparse.Namespace(skill=None, surface=RAG_MCP_SURFACE, hash=args.hash,
                           head=args.head, peer_attest_mode="")
    ))
    notice = commands.add_parser("authorize-notice-bot")
    notice.add_argument("--hash", required=True)
    notice.add_argument("--head", required=True)
    notice.set_defaults(run=lambda args: skill_gate.cmd_release_authorize(
        argparse.Namespace(skill=None, surface=NOTICE_BOT_SURFACE, hash=args.hash,
                           head=args.head, peer_attest_mode="")
    ))
    relay = commands.add_parser("authorize-omo-relay")
    relay.add_argument("--hash", required=True)
    relay.add_argument("--head", required=True)
    relay.set_defaults(run=lambda args: skill_gate.cmd_release_authorize(
        argparse.Namespace(skill=None, surface=OMO_RELAY_SURFACE, hash=args.hash,
                           head=args.head, peer_attest_mode="")
    ))
    auto = commands.add_parser("authorize-auto-write")
    auto.add_argument("--kind", required=True)
    auto.add_argument("--head", required=True)
    def authorize_auto(args: argparse.Namespace) -> int:
        from automation.auto_write_policy import surface

        try:
            name, digest = surface(args.kind)
        except ValueError:
            return 4
        return skill_gate.cmd_release_authorize(argparse.Namespace(
            skill=None, surface=name, hash=digest, head=args.head, peer_attest_mode="",
        ))
    auto.set_defaults(run=authorize_auto)
    ledger = commands.add_parser("authorize-ledger")
    ledger.add_argument("--head", required=True)
    def authorize_ledger(args: argparse.Namespace) -> int:
        from automation.ledger_policy import surface

        name, digest = surface()
        return skill_gate.cmd_release_authorize(argparse.Namespace(
            skill=None, surface=name, hash=digest, head=args.head, peer_attest_mode="",
        ))
    ledger.set_defaults(run=authorize_ledger)
    args = parser.parse_args(argv)
    return int(args.run(args))


if __name__ == "__main__":
    raise SystemExit(main())

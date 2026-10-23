#!/usr/bin/env python3
"""gws-calendar skill CLI (W3-1): read is gate-free, mutations are gated.

Exit codes: 0 ok | 1 confirmation absent/invalid (nothing executed)
            2 input rejected | 3 config/env error | 5 ambiguous time (re-ask)
            6 gws execution failed (after approval; recorded as failed)

Env: CALENDAR_GATE_DIR (~/.hermes/calendar-gate), CALENDAR_APPROVAL_LOG
     (/srv/autophagy-agents/logs/approvals.jsonl), CALENDAR_GWS_BIN,
     INTEROP_RUNTIME, INTEROP_CONFIG, E2E_TEST_MODE, INTEROP_E2E_SECRET,
     DISCORD_BOT_TOKEN (production DM-confirm path only).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path

from calendar_confirm_input import DraftRecord

_repo_root = Path(os.environ.get("AUTOPHAGY_REPO_ROOT", Path(__file__).resolve().parents[3]))
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

calendar_confirm = import_module("calendar_confirm")
calendar_core = import_module("calendar_core")
calendar_gate = import_module("calendar_gate")
calendar_output = import_module("calendar_output")
calendar_preflight = import_module("calendar_preflight")
calendar_routing = import_module("calendar_routing")
calendar_governed = import_module("calendar_governed")

ROUTING_REJECT_EXIT_CODE = 4


_print_draft = calendar_output.print_draft


def _origin_of(args: argparse.Namespace) -> dict[str, str]:
    """Where the instruction came from, so the result returns to that channel's thread."""
    return {name: str(getattr(args, name, "") or "") for name in ("origin_channel_id", "origin_message_id")}


def cmd_list(args: argparse.Namespace) -> int:
    now = datetime.now(calendar_core.KST)
    params = {
        "calendarId": args.calendar,
        "maxResults": args.max,
        "orderBy": "startTime",
        "singleEvents": True,
        "timeMax": (now + timedelta(days=args.days)).isoformat(),
        "timeMin": now.isoformat(),
    }
    if args.query:
        params["q"] = args.query
    result = subprocess.run(  # noqa: S603
        [calendar_gate.gws_bin(), "calendar", "events", "list", "--params",
         json.dumps(params, ensure_ascii=False)],
        capture_output=True, text=True, timeout=calendar_gate.GWS_TIMEOUT_S, check=False,
        cwd=str(Path.home()),
    )
    if result.returncode != 0:
        raise calendar_gate.GateError(f"gws list 실패: {result.stderr.strip()[:200]}", 6)
    items = json.loads(result.stdout).get("items", [])
    for item in items:
        start = item.get("start", {})
        print(
            f"EVENT id={item.get('id', '')} "
            f"start={start.get('dateTime', start.get('date', ''))} "
            f"summary={item.get('summary', '')}"
        )
    print(f"LISTED n={len(items)} calendar={args.calendar} days={args.days}")
    return 0


def _build_create_draft(args: argparse.Namespace) -> DraftRecord:
    now = datetime.now(calendar_core.KST)
    request_text = f"{args.text}\n{args.summary}"
    try:
        peer_ids = calendar_routing.named_peer_ids(request_text)
    except calendar_routing.PeerRegistryError as error:
        raise calendar_gate.GateError(f"피어 레지스트리 읽기 실패: {error}", 3) from error
    # Only a NAMED peer can trigger cross-skill routing. Without a peer this is
    # a plain solo request: fall through to parse_request, which owns the exit-5
    # ambiguity re-ask ("모호한 시간은 되묻는다") — the guard must not swallow it.
    if peer_ids:
        route = calendar_routing.classify_meeting_request(request_text, now)
        if route != "calendar":
            peer_label = peer_ids[0]
            if route == "coordination":
                detail = (
                    f"ROUTING-REJECT 상대({peer_label})와 시간 조율이 필요한 요청입니다 — "
                    "coordination 스킬을 사용하세요 (calendar는 본인 단독 일정 전용)."
                )
            else:  # clarify — fail-closed: do not create a draft, ask the owner
                detail = (
                    f"ROUTING-CLARIFY 상대({peer_label}) 관련 요청의 의도가 모호합니다 — "
                    "본인 단독 일정이면 정확한 시각을, 상대와 조율이면 조율 의사를 밝혀주세요 "
                    "(예: '오후 3시 미팅' vs 'peer-test와 오전에 가능한 시간 조율')."
                )
            raise calendar_gate.GateError(detail, ROUTING_REJECT_EXIT_CODE)
    request = calendar_core.parse_request(args.text, now, all_day_undated=_mail_all_day_undated())
    summary = args.summary or request.summary
    if not summary:
        raise calendar_core.ParseRejected("일정 제목을 알 수 없습니다 (--summary로 지정 가능)")
    request = calendar_core.ParsedRequest(
        summary=summary, start=request.start, end=request.end, all_day=request.all_day,
    )
    from automation.calendar_default_color import node_color

    try:
        color = node_color(Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser())
    except (OSError, ValueError) as error:
        raise calendar_gate.GateError("캘린더 기본색 설정이 잘못되었습니다", 3) from error
    argv = (
        calendar_core.build_create_argv(args.calendar, request, color_id=color)
        if color is not None else calendar_core.build_create_argv(args.calendar, request)
    )
    return calendar_gate.build_draft(
        action="create", argv=argv,
        calendar_id=args.calendar, event_id="", summary=summary,
        start=request.start.isoformat(), end=request.end.isoformat(),
        channel_id=args.channel_id, **_origin_of(args),
    )


#: Same name as ``automation.mail_schedule_all_day.CALENDAR_ENV`` (pinned by a unit test).
ALL_DAY_ENV = "CALENDAR_ALL_DAY_UNDATED"


def _mail_all_day_undated() -> bool:
    """Only the mail digest asks (env pair), and only the released marker says yes.

    The env is read before the switch module is imported, so an owner command (no env)
    never depends on that module being present in the code tree.
    """
    if os.environ.get(ALL_DAY_ENV) != "1":
        return False
    from automation.mail_schedule_all_day import node_mode

    try:
        return node_mode(Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()) == "on"
    except (OSError, ValueError) as error:
        raise calendar_gate.GateError("종일 일정 초안 설정이 잘못되었습니다", 3) from error


def cmd_draft_create(args: argparse.Namespace) -> int:
    record = _build_create_draft(args)
    # 다이제스트 건(--digest-day)은 저장에서 끝나지 않고 그날 승인 스레드에 카드까지 올린다.
    _print_draft(import_module("calendar_digest").submit(record, args.digest_day))
    return 0


def cmd_owner_create(args: argparse.Namespace) -> int:
    """The node gate has verified the owner message and release switch."""
    return import_module("calendar_owner").create(_build_create_draft(args))


def cmd_draft_update(args: argparse.Namespace) -> int:
    if not args.text and not args.summary:
        raise calendar_core.ParseRejected("--text(새 일시) 또는 --summary(새 제목) 중 하나는 필요합니다")
    request = None
    if args.text:
        request = calendar_core.parse_request(args.text, datetime.now(calendar_core.KST))
    body = calendar_core.patch_body(request, args.summary)
    record = calendar_gate.build_draft(
        action="update", argv=calendar_core.build_patch_argv(args.calendar, args.event_id, body),
        calendar_id=args.calendar, event_id=args.event_id,
        summary=args.summary or (request.summary if request else ""),
        start=request.start.isoformat() if request else "",
        end=request.end.isoformat() if request else "",
        channel_id=args.channel_id, **_origin_of(args),
    )
    _print_draft(import_module("calendar_card").capture_context(record))
    return 0


def cmd_draft_delete(args: argparse.Namespace) -> int:
    record = calendar_gate.build_draft(
        action="delete", argv=calendar_core.build_delete_argv(args.calendar, args.event_id),
        calendar_id=args.calendar, event_id=args.event_id, summary=args.label,
        start="", end="", channel_id=args.channel_id, **_origin_of(args),
    )
    _print_draft(import_module("calendar_card").capture_context(record))
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    draft = calendar_gate.load_draft(args.draft)
    if args.injection_file and args.watch_authorization:
        raise calendar_gate.GateError("확인 경로를 둘 이상 지정할 수 없습니다", 1)
    if args.watch_authorization:
        if os.environ.get("E2E_TEST_MODE"):
            raise calendar_gate.GateError("E2E_TEST_MODE에서 watcher 승인을 사용할 수 없습니다", 3)
        ref = calendar_confirm.consume_watcher_authorization(draft, Path(args.watch_authorization))
        method = "owner_dm_reaction"
    elif args.injection_file:
        ref = calendar_confirm.confirm_via_injection(draft, Path(args.injection_file))
        method = "signed_injection_e2e"
    else:
        if os.environ.get("E2E_TEST_MODE"):
            raise calendar_gate.GateError(
                "E2E_TEST_MODE인데 --injection-file이 없음 — 모호한 모드 거부", 3
            )
        calendar_confirm.reject_cancel_reaction(draft)
        try:
            ref = calendar_confirm.confirm_via_owner_scan(draft)
            method = "owner_dm_reply"
        except calendar_gate.GateError as error:
            if error.exit_code != 1:
                raise
            ref = calendar_confirm.confirm_via_reaction(draft)
            method = "owner_dm_reaction"
    approval = calendar_gate.Approval(ref=ref, method=method, owner=calendar_confirm.owner_id())
    try:
        event_id = calendar_preflight.guarded_execute_draft(draft, approval)
    except calendar_preflight.CalendarPreflightError as error:
        if error.should_render:
            print(error)
        raise calendar_gate.GateError(str(error), error.exit_code) from None
    calendar_confirm.clear_pending(args.draft)
    print(f"EXECUTED action={draft['action']} event={event_id or '-'} method={method} ref={ref}")
    return 0


def cmd_discard(args: argparse.Namespace) -> int:
    calendar_gate.discard_draft(args.draft)
    calendar_confirm.clear_pending(args.draft)
    print(f"DISCARDED draft={args.draft}")
    return 0


cmd_list_drafts = calendar_output.cmd_list_drafts


cmd_post_confirm = calendar_output.cmd_post_confirm


def cmd_sign(args: argparse.Namespace) -> int:
    draft = calendar_gate.load_draft(args.draft)
    calendar_confirm.sign_injection(
        draft, Path(args.out), args.user_id or None, args.channel_id or None,
        args.forge_signature,
    )
    print(f"SIGNED draft={args.draft} out={args.out} forged={args.forge_signature}")
    return 0


def _add_common(parser: argparse.ArgumentParser, *, owner_command: bool = False) -> None:
    parser.add_argument("--calendar", default="primary")
    parser.add_argument("--channel-id", default="dm")
    # 지시가 시작된 채널/메시지 — 결과 통지를 그 스레드로 돌려보내기 위한 것뿐이며,
    # 승인 표면(확인 메시지)에도 초안 해시에도 영향을 주지 않는다.
    parser.add_argument("--origin-channel-id", default="", required=owner_command)
    parser.add_argument("--origin-message-id", default="", required=owner_command)


def cmd_digest_pending(args: argparse.Namespace) -> int:
    """Read only: undecided digest event cards per thread, for the digest thread's mark."""
    counts = import_module("calendar_digest").pending_by_thread(list(args.thread))
    for thread_id, count in counts.items():
        print(f"DIGEST-PENDING thread={thread_id} count={count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calendar", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="일정 조회 (읽기 전용, 게이트 불요)")
    listing.add_argument("--days", type=int, default=7)
    listing.add_argument("--max", type=int, default=20)
    listing.add_argument("--query", default="")
    listing.add_argument("--calendar", default="primary")
    listing.set_defaults(func=cmd_list)

    for command, handler in (("draft-create", cmd_draft_create), ("owner-create", cmd_owner_create)):
        create = sub.add_parser(command, help="일정 생성 초안 / 검증된 소유자 명령 실행")
        create.add_argument("--text", required=True, help="자연어 요청 (예: 내일 오후 3시 실험 미팅)")
        create.add_argument("--summary", default="", help="제목 명시 (자연어 파싱 대신)")
        if command == "draft-create":
            create.add_argument("--digest-day", default="", help="메일 다이제스트 게시일 YYYY-MM-DD — 그날 승인 스레드에 카드까지 게시")
        _add_common(create, owner_command=command == "owner-create")
        create.set_defaults(func=handler)

    update = sub.add_parser("draft-update", help="수정 초안")
    update.add_argument("--event-id", required=True)
    update.add_argument("--text", default="", help="새 일시 자연어")
    update.add_argument("--summary", default="", help="새 제목")
    _add_common(update)
    update.set_defaults(func=cmd_draft_update)

    delete = sub.add_parser("draft-delete", help="삭제 초안")
    delete.add_argument("--event-id", required=True)
    delete.add_argument("--label", default="", help="확인 표시용 제목 라벨")
    _add_common(delete)
    delete.set_defaults(func=cmd_draft_delete)

    confirm = sub.add_parser("confirm", help="소유자 확인 검증 후에만 실행")
    confirm.add_argument("--draft", required=True)
    confirm.add_argument("--injection-file", default="")
    confirm.add_argument("--watch-authorization", default="", help=argparse.SUPPRESS)
    confirm.set_defaults(func=cmd_confirm)

    post_confirm = sub.add_parser("post-confirm", help="반응 확인 DM 게시 및 pending 저장")
    post_confirm.add_argument("--draft", required=True)
    post_confirm.set_defaults(func=cmd_post_confirm)

    discard = sub.add_parser("discard", help="초안 폐기 (확인 거부)")
    discard.add_argument("--draft", required=True)
    discard.set_defaults(func=cmd_discard)

    sub.add_parser("list-drafts", help="초안 목록").set_defaults(func=cmd_list_drafts)

    pending = sub.add_parser("digest-pending", help="다이제스트 스레드별 미결 일정 카드 수 (읽기 전용)")
    pending.add_argument("--thread", action="append", default=[], required=True)
    pending.set_defaults(func=cmd_digest_pending)

    sign = sub.add_parser("sign", help="E2E 전용: 서명된 주입 승인 생성")
    sign.add_argument("--draft", required=True)
    sign.add_argument("--out", required=True)
    sign.add_argument("--user-id", default="")
    sign.add_argument("--channel-id", default="")
    sign.add_argument("--forge-signature", action="store_true")
    sign.set_defaults(func=cmd_sign)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        calendar_governed.guard(Path(__file__), args.command)
        return int(args.func(args))
    except calendar_core.AmbiguousTime as error:
        print(f"AMBIGUOUS-TIME 되묻기: {error.question}", file=sys.stderr)
        return 5
    except calendar_core.ParseRejected as error:
        print(f"INPUT-REJECTED {error}", file=sys.stderr)
        return 2
    except calendar_gate.GateError as error:
        print(f"GATE-REFUSED {error}", file=sys.stderr)
        return error.exit_code
    except FileNotFoundError as error:
        print(f"GATE-REFUSED 파일 없음: {error.filename}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

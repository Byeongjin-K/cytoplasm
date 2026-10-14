#!/usr/bin/env python3
"""mail triage CLI (W4-2/W4-6): owner-instruction reply drafting→owner
confirmation→send pipeline. Drafting is owner-initiated (`draft` — 지시문
필수); the cron `watch` tick is the approval/send loop ONLY (repost→✅/⛔
resolution→send — no auto-drafting). Draft pipeline order (fixed):
① classification annotation ② Korean reply draft through the account model chain
③ owner gate ④ mailon send ⑤ approvals.jsonl (W0-6 schema). Schedule-needed mail is delegated to
the W3-1 calendar skill via the legacy manual `process` path (draft only —
its own gate confirms; SQLite claim-before-draft keeps its ticks idempotent).

Exit codes: 0 ok | 1 approval absent/invalid (nothing sent) | 2 input rejected
            3 config/env/mode error | 4 mail read failed | 6 send failed

Env: TRIAGE_GATE_DIR, TRIAGE_DB, TRIAGE_APPROVAL_LOG, TRIAGE_MAIL_HOME,
     TRIAGE_MAIL_MODE_FILE, TRIAGE_MAIL_MODE_REPO,
     TRIAGE_CLASSIFY_PROMPT, TRIAGE_REPLY_PROMPT, TRIAGE_CALENDAR_CLI,
     TRIAGE_MAILON_PYTHON,
     INTEROP_RUNTIME, INTEROP_CONFIG, E2E_TEST_MODE, INTEROP_E2E_SECRET
     (+ shared-client test hooks AUTOPHAGY_HERMES_BIN —
     never in production).

The account config selects the primary and fallback models. If the whole chain
is unavailable the call fails closed: LLM-FAIL with a non-zero exit, never a downgrade.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import triage_approval
import triage_confirm
import triage_core
import triage_digest
import triage_gate
import triage_llm
import triage_mode
import mail_drive_share
import mail_forward
import mail_quote
import mail_send_retry
import mail_runtime
import triage_pipeline
import mail_preflight
import triage_store
from triage_transport import _get_mail

mail_evidence = importlib.import_module("mail_evidence")
mail_knowledge = importlib.import_module("mail_knowledge")

DEFAULT_REPLY_PROMPT = triage_pipeline.DEFAULT_REPLY_PROMPT


def cmd_process(args: argparse.Namespace) -> int:
    return triage_pipeline.run_process(args)


def _collect_private_evidence(
    draft: dict[str, str], counterparty: str, subject: str, material: str,
) -> object:
    if not mail_preflight.ensure_cli_evidence_query(draft):
        return mail_knowledge.unavailable(counterparty, subject, material)
    return mail_knowledge.collect(counterparty, subject, material)


def cmd_draft(args: argparse.Namespace) -> int:
    """Owner-instruction draft: 지시가 초안 여부를 결정하고 분류는 주석일 뿐이다."""
    if triage_mode.effective_mode() == "no-go":
        raise triage_gate.GateError("mail-mode=no-go — W4-2 파이프라인 비활성(W4-1N 분기)", 3)
    if triage_gate.has_draft_for(args.uid):
        raise triage_gate.GateError(
            f"uid(불투명)={triage_core.mask_value(args.uid)} 초안이 이미 있음 — "
            "먼저 discard 후 다시 시도", 2,
        )
    detail = _get_mail(args.uid)
    pack = None
    if bool(getattr(args, "with_evidence", False)):
        to = triage_core.extract_reply_address(str(detail.get("sender") or ""))
        if to:
            subject = str(detail.get("subject") or "")
            counterparty = str(detail.get("sender") or to)
            pack = _collect_private_evidence(
                {
                    "id": f"evidence:{args.uid}", "to": to, "subject": subject,
                    "body": str(detail.get("body") or ""),
                },
                counterparty, subject, args.instruction,
            )
    cls = triage_pipeline._classify(args.uid, detail)
    actions = triage_pipeline._draft_and_post(
        {**detail, "uid": args.uid}, cls,
        post=not args.no_post, instruction=args.instruction,
        attachments=tuple(getattr(args, "attachment", ()) or ()),
        evidence_pack=pack,
        reply_all=bool(getattr(args, "reply_all", False)),
    )
    if "reply-no-address" in actions:
        raise triage_gate.GateError("회신 주소를 찾을 수 없음 — 초안 생성 불가", 2)
    draft_id = actions[0].removeprefix("draft:")
    if pack is not None:
        mail_evidence.write_sidecar(triage_gate.gate_dir(), draft_id, pack)
    triage_store.record_processed(
        triage_gate.db_path(), args.uid, category=cls.category,
        action=f"instr-draft:{draft_id}", processed_at=triage_core.utc_now(),
    )
    posted = int(any(action.startswith("posted:") for action in actions))
    print(f"DRAFTED draft={draft_id} posted={posted}")
    if posted:
        _print_thread_line(triage_gate.load_draft(draft_id))
    return 0


def _forward_source(args: argparse.Namespace) -> mail_forward.ForwardSource | None:
    """The forwarded original (FW) — a new mail plus the original's quote and files."""
    uid = str(getattr(args, "forward", "") or "")
    message_id = str(getattr(args, "forward_gmail", "") or "")
    if uid:
        return mail_forward.from_mailon(uid, _get_mail(uid))
    if message_id:
        return mail_forward.from_gmail(message_id)
    return None


def cmd_compose(args: argparse.Namespace) -> int:
    if triage_mode.effective_mode() == "no-go":
        raise triage_gate.GateError("mail-mode=no-go — W4-2 파이프라인 비활성(W4-1N 분기)", 3)
    forwarding = bool(getattr(args, "forward", "") or getattr(args, "forward_gmail", ""))
    if not forwarding and (args.subject is None or args.body is None):
        raise triage_gate.GateError("새 메일에는 --subject 와 --body 가 필요합니다 (전달은 --forward)", 2)
    drive_path = str(getattr(args, "drive_share", "") or "")
    share_with = tuple(getattr(args, "share_with", ()) or ())
    if not drive_path and (share_with or getattr(args, "drive_folder", "")):
        raise triage_gate.GateError("--share-with · --drive-folder 는 --drive-share 와 함께만 씁니다", 2)
    share = (
        mail_drive_share.prepare(drive_path, share_with, body=args.body or "",
                                 project=str(getattr(args, "drive_folder", "") or ""))
        if drive_path else None
    )
    forward = None
    try:
        forward = _forward_source(args)
        return _compose(args, forward, share)
    except BaseException:
        if forward is not None:
            forward.discard()
        if share is not None:
            mail_drive_share.discard(share)
        raise


def _compose(
    args: argparse.Namespace, forward: mail_forward.ForwardSource | None, share: dict | None = None,
) -> int:
    subject = args.subject if args.subject is not None else (forward.subject if forward else "")
    body = args.body if args.body is not None else ""
    cc = ", ".join(getattr(args, "cc", ()) or ())
    pack = None
    if bool(getattr(args, "with_evidence", False)):
        pack = _collect_private_evidence(
            {
                "id": "evidence:compose", "to": args.to, "cc": cc,
                "subject": subject, "body": body,
            },
            args.to, subject, body,
        )
    in_reply_to = str(getattr(args, "in_reply_to", "") or "")
    if forward is not None:
        quote = forward.quote
    elif in_reply_to:
        quote = mail_quote.render_quote(mail_quote.parse_original(_get_mail(in_reply_to)))
    else:
        quote = ""
    extra = tuple(getattr(args, "attachment", ()) or ())
    draft = triage_pipeline.compose_and_post(
        args.to, subject, body, post=not args.no_post,
        attachments=(*forward.attachments, *extra) if forward else extra, cc=cc,
        evidence_pack=pack,
        origin_channel_id=str(getattr(args, "origin_channel_id", "") or ""),
        origin_message_id=str(getattr(args, "origin_message_id", "") or ""),
        quote=quote,
        forward=forward.record() if forward else None,
        drive_share=share,
    )
    if pack is not None:
        mail_evidence.write_sidecar(triage_gate.gate_dir(), str(draft["id"]), pack)
    posted = int(bool(draft.get("message_id")))
    print(f"COMPOSED draft={draft['id']} posted={posted}")
    if forward is not None:
        print(f"FORWARD draft={draft['id']} source={forward.origin} attachments={len(forward.attachments)}")
    if share is not None:
        print(f"DRIVE-SHARE draft={draft['id']} file={share['drive_name']} "
              f"people={len(share['grantees'])} role={share['role']}")
    _print_thread_line(draft)
    return 0


def _print_thread_line(draft: dict[str, object]) -> None:
    if not draft.get("message_id"):
        return
    try:
        from automation.interop.thread_pointer import approval_thread_line
    except ImportError as error:
        print(f"APPROVAL-THREAD-UNAVAILABLE draft={draft['id']} err={type(error).__name__}",
              file=sys.stderr)
        return
    print(approval_thread_line("draft", str(draft["id"]), draft))


def cmd_evidence(args: argparse.Namespace) -> int:
    pack = _collect_private_evidence(
        {
            "id": "evidence:preview", "to": args.counterparty,
            "subject": args.subject, "body": args.material,
        },
        args.counterparty, args.subject, args.material,
    )
    print(mail_evidence.preview(pack, as_json=args.json))
    return 0


def cmd_reply_request(args: argparse.Namespace) -> int:
    """Owner reply on a digest mail message → that mail's reply draft (same draft gate).

    The gateway shows the agent the replied-to message text, which carries the
    ``회신 키``; the key maps to the uid through ``digest_items``. Only the owner's
    reply counts, and one mail gets one draft: the uid check runs under a lock so
    two replies racing on the same mail cannot both pass ``has_draft_for``.
    """
    import fcntl

    if str(args.author_id).strip() != triage_confirm.owner_id():
        raise triage_gate.GateError("REPLY-REQUEST-IGNORED not-owner — 소유자 답장만 회신 요청으로 받는다", 2)
    item = triage_store.digest_item_by_reply_key(triage_gate.db_path(), args.key.strip())
    if item is None:
        raise triage_gate.GateError(f"REPLY-REQUEST-UNKNOWN key={args.key} — 다이제스트에 없는 회신 키", 3)
    print(f"REPLY-REQUEST key={args.key.strip()} item={item['item_no']}")
    lock_dir = triage_gate.gate_dir()
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (lock_dir / "reply-request.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return cmd_draft(argparse.Namespace(
            uid=str(item["uid"]), instruction=args.instruction, attachment=[],
            no_post=args.no_post, with_evidence=args.with_evidence, reply_all=args.reply_all,
        ))


def cmd_digest(args: argparse.Namespace) -> int:
    if triage_mode.effective_mode() == "no-go":
        raise triage_gate.GateError("mail-mode=no-go — 다이제스트 비활성(W4-1N 분기)", 3)
    return triage_digest.run_digest(
        limit=args.limit, sync=not args.no_sync, dry_run=args.dry_run
    )


def cmd_digest_items(args: argparse.Namespace) -> int:
    items = triage_store.latest_digest_items(triage_gate.db_path(), args.run)
    if not items:
        run_label = f" run={args.run}" if args.run is not None else ""
        raise triage_gate.GateError(f"다이제스트 항목 없음{run_label}", 3)
    for item in items:
        print(
            f"ITEM no={item['item_no']} uid={item['uid']} "
            f"category={item['category']} "
            f"flags={item['flags']} subject={item['subject']}"
        )
    return 0


_DM_NOTIFIABLE_METHODS: Final = frozenset({"manual_reaction"})


def _remind_pending(draft: dict, config: object | None) -> None:
    if config is None:
        return
    lifecycle = triage_approval.lifecycle()
    reminder = triage_approval._repo_module("approval_reminder")
    lease_module = triage_approval._lease_module()
    guild_id = draft.get("approval_guild_id")
    guild_id_for = reminder.channel_guild_resolver(triage_confirm.fetch_channel)
    context = reminder.ReminderContext(
        config=config,
        journal=lease_module.ReminderJournal(triage_gate.gate_dir() / "reminder-journal"),
        request_type=triage_approval.triage_binding.approval_kind(draft),
        deliver=lambda channel_id, content: triage_confirm.post_approval_request(
            content, channel_id
        ),
        guild_id_for=guild_id_for,
        clock=lambda: datetime.now(UTC),
        guild_id=guild_id if isinstance(guild_id, str) and guild_id else None,
        space_for=lambda _channel: reminder.stored_reminder_space(draft.get("surface")),
    )
    lifecycle.remind_owner_approval(
        triage_approval.request_of(draft),
        triage_approval.MailApprovalGate(draft),
        triage_approval.confirm_lease(),
        context,
    )


def _notify_sent(draft: dict, method: str) -> None:
    """Best-effort owner notification after a committed send (owner request 2026-07-20).

    승인 provenance로 게이트된다 — **소유자 리액션 승인만** DM을 낼 수 있고, 주입·테스트
    승인(E2E/샌드박스)은 fail-closed로 거부된다(allowlist: 새 승인 방식은 여기에 명시적으로
    등록되기 전까지 알림 없음). scenario.sh는 조작된 owner id로 서명 주입 승인을 만들어
    배포 노드의 진짜 봇 토큰으로 실제 DM 채널을 열게 했다.

    The guard lives here, NOT in triage_confirm.notify_result: the cancel notice
    (`_notify_cancelled`) is a legitimate caller that carries no provenance —
    gating the transport itself would mute it too.

    The send is already executed; NO failure here may fail the tick or make a
    committed draft look unsent — hence the broad catch (repo precedent
    run_process).
    """
    if method not in _DM_NOTIFIABLE_METHODS:
        print(f"NOTIFY-SKIP draft={draft['id']} reason={method}", file=sys.stderr)
        return
    try:
        triage_confirm.notify_result(
            draft,
            f"✉️ 발송 완료: {draft['subject']} → {draft['to']} (draft {draft['id']})\n"
            "소유자 ✅ 승인으로 발송되었습니다.",
            outcome=triage_confirm.SENT_OUTCOME,
        )
    except Exception as error:  # noqa: BLE001 — notification must never break the tick
        print(f"NOTIFY-FAIL draft={draft['id']} "
              f"err={triage_core.redact(str(error))[:120]}", file=sys.stderr)


def _notify_failed(draft: dict, error: triage_gate.GateError) -> None:
    """Best-effort failure notice in the draft's approval thread; same rules as
    _notify_sent. The stored record carries the step and cause the send recorded."""
    stored = draft
    path = triage_gate._draft_path(draft["id"])
    if path is not None and path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = draft
    try:
        triage_confirm.notify_result(
            stored, mail_send_retry.failure_text(stored, triage_core.redact(str(error))[:200]))
    except Exception as notify_error:  # noqa: BLE001 — notification must never break the tick
        print(f"NOTIFY-FAIL draft={draft['id']} "
              f"err={triage_core.redact(str(notify_error))[:120]}", file=sys.stderr)


def _notify_cancelled(draft: dict) -> None:
    """Best-effort cancel notice — the discard is already committed.

    _notify_sent와 같은 규칙: 통지 실패가 tick을 죽여서는 안 된다(기존 dm_owner
    직호출은 try 밖이라 전송 실패 시 tick 전체가 죽는 잠재 결함 — 2026-08-22 수리).
    취소 통지는 승인 provenance를 갖지 않는 정당한 호출자다.
    """
    try:
        triage_confirm.notify_result(
            draft,
            f"⛔ 발송 취소: {draft['subject']} → {draft['to']} (draft {draft['id']})\n"
            "소유자 ⛔ 리액션으로 취소되어 메일은 발송되지 않았습니다.",
            outcome=triage_confirm.CANCELLED_OUTCOME,
        )
    except Exception as error:  # noqa: BLE001 — notification must never break the tick
        print(f"NOTIFY-FAIL draft={draft['id']} "
              f"err={triage_core.redact(str(error))[:120]}", file=sys.stderr)


def cmd_watch(_args: argparse.Namespace) -> int:
    """Production cron tick: repost pending, resolve ✅/⛔, send approved (초안 생성 없음)."""
    if os.environ.get("E2E_TEST_MODE"):
        raise triage_gate.GateError("watch는 프로덕션 전용 — E2E_TEST_MODE 거부", 3)
    mode = triage_mode.effective_mode()
    if mode != "full-go":
        print(f"MODE-SKIP mode={mode} (W4-1N 분기 — 자동 발송 정지)")
        return 0
    owner = triage_confirm.owner_id()
    failed_rc = 0
    for draft in triage_gate.list_drafts():
        if draft.get("status") != "pending":
            continue
        try:
            if triage_approval.expire_retired_approval(draft):
                print(f"EXPIRED draft={draft['id']} reason=approval-surface-retired")
                continue
        except triage_gate.GateError as error:
            if error.exit_code != 1:
                raise
            continue
        if not draft.get("message_id"):
            draft = {**draft, "message_id": triage_pipeline._post_draft_for_approval(draft)}
            print(f"REPOSTED draft={draft['id']} message={draft['message_id']}")
        try:
            _remind_pending(draft, getattr(_args, "reminder_config", None))
            action = triage_confirm.resolve_reaction(draft)
        except triage_gate.GateError as error:
            if error.exit_code != 1:
                raise
            continue  # 승인 없음 → pending 유지, 발송 0건
        except OSError as error:  # 429/네트워크 등 일시 오류 — draft 단위 격리, 다음 tick 재시도
            print(f"REACTION-RETRY draft={draft['id']} "
                  f"err={triage_core.redact(str(error))[:120]}", file=sys.stderr)
            continue
        if action == triage_confirm.CANCEL_EMOJI:
            triage_gate.discard_draft(draft["id"])
            _notify_cancelled(draft)
            print(f"CANCELLED draft={draft['id']} method=manual_reaction")
            continue
        if action != triage_confirm.APPROVE_EMOJI:
            continue
        waiting = mail_send_retry.waiting_until(draft, datetime.now(UTC))
        if waiting:
            print(f"RETRY-WAIT draft={draft['id']} until={waiting}")
            continue
        ref = f"reaction:{draft['message_id']}"
        try:
            mail_preflight.execute_cli_draft(draft, triage_gate.Approval(
                ref=ref, method="manual_reaction", owner=owner,
            ))
        except triage_gate.GateError as error:
            # An approved send that failed is a failed tick, told in the draft's own
            # thread; the other drafts still get their turn (2026-10-13).
            failed_rc = max(failed_rc, error.exit_code)
            _notify_failed(draft, error)
            print(f"SEND-FAILED draft={draft['id']} rc={error.exit_code} {error}",
                  file=sys.stderr)
            continue
        _notify_sent(draft, "manual_reaction")
        print(f"SENT draft={draft['id']} method=manual_reaction ref={ref}")
    return failed_rc


def cmd_confirm(args: argparse.Namespace) -> int:
    draft = triage_gate.load_draft(args.draft)
    if args.injection_file:
        ref = triage_confirm.confirm_via_injection(draft, Path(args.injection_file))
        method = "signed_injection_e2e"
    else:
        if os.environ.get("E2E_TEST_MODE"):
            raise triage_gate.GateError(
                "E2E_TEST_MODE인데 --injection-file이 없음 — 모호한 모드 거부", 3
            )
        ref = triage_confirm.confirm_via_reaction(draft)
        method = "manual_reaction"
    mail_preflight.execute_cli_draft(draft, triage_gate.Approval(
        ref=ref, method=method, owner=triage_confirm.owner_id(),
    ))
    _notify_sent(draft, method)
    print(f"SENT draft={args.draft} method={method} ref={ref}")
    return 0


def cmd_discard(args: argparse.Namespace) -> int:
    triage_gate.discard_draft(args.draft)
    print(f"DISCARDED draft={args.draft}")
    return 0


def cmd_list_drafts(_args: argparse.Namespace) -> int:
    for record in triage_gate.list_drafts():
        print(f"DRAFT id={record['id']} status={record['status']} "
              f"uid={record['uid_opaque']} "
              f"message={record.get('message_id') or 'unposted'} created={record['created']}")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    draft = triage_gate.load_draft(args.draft)
    triage_confirm.sign_injection(
        draft, Path(args.out), args.user_id or None, args.channel_id or None,
        args.forge_signature,
    )
    print(f"SIGNED draft={args.draft} out={args.out} forged={args.forge_signature}")
    return 0


def cmd_mode(_args: argparse.Namespace) -> int:
    print(f"MODE effective={triage_mode.effective_mode()} "
          f"runtime={triage_mode.runtime_mode_file()} seed={triage_mode.repo_mode_file()} "
          f"consecutive_send_failures={triage_store.consecutive_send_failures(triage_gate.db_path())}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    """Read via the existing masking boundary; only project a disclosed body."""
    import io
    import json
    from contextlib import redirect_stdout

    import mail_body_view
    import mail_wrapper

    if args.masked or not args.body:
        return mail_wrapper.cmd_get(args)
    with io.StringIO() as buffer:
        with redirect_stdout(buffer):
            code = mail_wrapper.cmd_get(args)
        output = buffer.getvalue()
    if code:
        sys.stdout.write(output)
        return code
    mail: mail_body_view.Mail = json.loads(output)["mail"]
    if args.raw:
        sys.stdout.write(mail.get("body") or "")
    else:
        print("\n\n".join(mail_body_view.render(mail, full=args.full)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mail-triage", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    get = sub.add_parser("get", help="단건 읽기 — 본문은 Discord용 청크로 표시")
    get.add_argument("uid")
    get.add_argument("--body", action="store_true")
    get.add_argument("--masked", action="store_true", help="기존 wrapper 마스킹 JSON 유지")
    view = get.add_mutually_exclusive_group()
    view.add_argument("--raw", action="store_true", help="공개된 본문을 가공 없이 출력")
    view.add_argument("--full", action="store_true", help="원문 인용도 표시")
    get.set_defaults(func=cmd_get)

    process = sub.add_parser(
        "process",
        help="신규 메일 triage (게이트→분류→초안→게시) (legacy 수동 — cron은 더 이상 자동 초안 생성 안 함)",
    )
    process.add_argument("--limit", type=int, default=10)
    process.add_argument("--no-sync", action="store_true", help="mailon sync 생략 (state.db만)")
    process.add_argument("--no-post", action="store_true", help="초안만 만들고 승인 메시지 게시 생략")
    process.set_defaults(func=cmd_process)

    draft = sub.add_parser("draft", help="소유자 지시 기반 회신 초안 (분류는 주석 — 게이트 아님)")
    draft.add_argument("--uid", required=True)
    draft.add_argument("--instruction", required=True, help="회신에 반영할 소유자 지시문")
    draft.add_argument(
        "--attachment", action="append", default=[], metavar="PATH",
        help="첨부할 로컬 파일 경로 (여러 번 지정 가능)",
    )
    draft.add_argument("--no-post", action="store_true", help="초안만 만들고 승인 메시지 게시 생략")
    draft.add_argument("--with-evidence", action="store_true", help="상대·주제 관련 개인 근거 사용")
    draft.add_argument(
        "--reply-all", action="store_true",
        help="전체회신 — 원문의 받는 사람·참조 전원을 Cc 에 넣는다 (소유자·발신자 제외)",
    )
    draft.set_defaults(func=cmd_draft)

    reply = sub.add_parser(
        "reply-request",
        help="다이제스트 메일 메시지에 단 소유자 답장 → 그 메일의 회신 초안 (draft 승인 경로 재사용)",
    )
    reply.add_argument("--key", required=True, help="답장 대상 메시지의 회신 키 (예: 1008-0800-3)")
    reply.add_argument("--author-id", required=True, help="답장한 Discord 사용자 id")
    reply.add_argument("--instruction", required=True, help="답장 본문 = 회신 요지")
    reply.add_argument("--no-post", action="store_true", help="초안만 만들고 승인 메시지 게시 생략")
    reply.add_argument("--with-evidence", action="store_true", help="상대·주제 관련 개인 근거 사용")
    reply.add_argument("--reply-all", action="store_true", help="전체회신")
    reply.set_defaults(func=cmd_reply_request)

    compose = sub.add_parser(
        "compose", help="새 메일 작성 초안 — 소유자 확정 게이트 (같은 watch cron 재사용)"
    )
    compose.add_argument("--to", required=True)
    compose.add_argument(
        "--cc", action="append", default=[], metavar="ADDRESS",
        help="참조 수신자 주소 (여러 번 지정 가능)",
    )
    compose.add_argument("--subject", default=None, help="새 메일은 필수 — 전달은 생략 시 'FW: <원 제목>'")
    compose.add_argument("--body", default=None, help="새 메일은 필수 — 전달은 원문 위에 붙는 메모(생략 가능)")
    compose.add_argument(
        "--attachment", action="append", default=[], metavar="PATH",
        help="첨부할 로컬 파일 경로 (여러 번 지정 가능)",
    )
    compose.add_argument("--no-post", action="store_true", help="초안만 만들고 승인 메시지 게시 생략")
    compose.add_argument("--with-evidence", action="store_true", help="상대·주제 관련 개인 근거 사용")
    compose.add_argument(
        "--origin-channel-id", default="",
        help="발신 지시를 받은 원 채널 id — 발송/취소 결과를 이 채널의 스레드로 통지",
    )
    compose.add_argument(
        "--origin-message-id", default="",
        help="원 채널의 지시 메시지 id — 있으면 그 메시지에 결과 스레드를 앵커",
    )
    origin_mail = compose.add_mutually_exclusive_group()
    origin_mail.add_argument(
        "--in-reply-to", default="", metavar="UID",
        help="후속 메일 — 이 uid 메일의 원문(헤더+본문)을 발송 본문 하단에 인용",
    )
    origin_mail.add_argument(
        "--forward", default="", metavar="UID",
        help="전달(FW) — 이 기관메일 uid 의 원문을 인용하고 원 첨부를 바이트 그대로 첨부",
    )
    origin_mail.add_argument(
        "--forward-gmail", default="", metavar="MESSAGE_ID",
        help="전달(FW) — 이 Gmail 메시지의 원문을 인용하고 원 첨부를 바이트 그대로 첨부",
    )
    compose.add_argument(
        "--drive-share", default="", metavar="PATH",
        help="메일 첨부로 보낼 수 없는 파일 — ✅ 뒤 Drive 에 올려 --share-with 사람에게만 보기 권한을 주고 "
             f"본문의 `{mail_drive_share.PLACEHOLDER}` 자리에 링크를 넣어 보낸다",
    )
    compose.add_argument(
        "--share-with", action="append", default=[], metavar="EMAIL",
        help="Drive 파일을 볼 사람의 이메일 (여러 번 지정 가능, 링크 공개는 하지 않는다)",
    )
    compose.add_argument(
        "--drive-folder", default="", metavar="NAME",
        help="Drive 공유 폴더 한 단 — autophagy/공유/<NAME>/<연도>/ (생략 시 autophagy/공유/<연도>/)",
    )
    compose.set_defaults(func=cmd_compose)

    evidence = sub.add_parser("evidence", help="상대·주제 관련 근거 미리보기")
    evidence.add_argument("--counterparty", required=True)
    evidence.add_argument("--subject", required=True)
    evidence.add_argument("--material", default="")
    evidence.add_argument("--json", action="store_true")
    evidence.set_defaults(func=cmd_evidence)

    digest = sub.add_parser("digest", help="기관메일 다이제스트 개인 메시지 생성 (읽기 전용)")
    digest.add_argument("--limit", type=int, default=20)
    digest.add_argument("--no-sync", action="store_true", help="mailon sync 생략 (state.db만)")
    digest.add_argument("--dry-run", action="store_true", help="전송·저장 없이 다이제스트 미리보기")
    digest.set_defaults(func=cmd_digest)

    digest_items = sub.add_parser("digest-items", help="최근 다이제스트 항목 조회 (마스킹)")
    digest_items.add_argument("--run", type=int, default=None, help="조회할 다이제스트 run id")
    digest_items.set_defaults(func=cmd_digest_items)

    watch = sub.add_parser("watch", help="프로덕션 cron tick: 승인/취소 처리 + 승인건 발송 (자동 초안 생성 없음)")
    watch.add_argument("--limit", type=int, default=10, help=argparse.SUPPRESS)
    watch.add_argument("--no-sync", action="store_true", help=argparse.SUPPRESS)
    watch.add_argument("--no-post", action="store_true", help=argparse.SUPPRESS)
    watch.set_defaults(func=cmd_watch)

    confirm = sub.add_parser("confirm", help="소유자 승인 검증 후에만 발송")
    confirm.add_argument("--draft", required=True)
    confirm.add_argument("--injection-file", default="")
    confirm.set_defaults(func=cmd_confirm)

    discard = sub.add_parser("discard", help="초안 폐기 (승인 거부, 발송 0건)")
    discard.add_argument("--draft", required=True)
    discard.set_defaults(func=cmd_discard)

    sub.add_parser("list-drafts", help="초안 목록 (마스킹)").set_defaults(func=cmd_list_drafts)
    sub.add_parser("mode", help="유효 mail-mode + 실패 카운터").set_defaults(func=cmd_mode)

    sign = sub.add_parser("sign", help="E2E 전용: 서명된 주입 승인 생성")
    sign.add_argument("--draft", required=True)
    sign.add_argument("--out", required=True)
    sign.add_argument("--user-id", default="")
    sign.add_argument("--channel-id", default="")
    sign.add_argument("--forge-signature", action="store_true")
    sign.set_defaults(func=cmd_sign)
    return parser


# 읽기 전용 서브커맨드 — 낡은 사본이 읽어도 외부효과가 없다. 나머지는 관리자 배포본에서만 돈다.
_READ_ONLY_COMMANDS = (cmd_mode, cmd_list_drafts, cmd_digest_items, cmd_evidence, cmd_get)


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.func not in _READ_ONLY_COMMANDS:
            refusal = mail_runtime.governed_copy_refusal(Path(__file__))
            if refusal:
                raise triage_gate.GateError(refusal, 3)
        if args.func is cmd_watch:
            config_module = triage_approval._repo_module("approval_reminder_config")
            args.reminder_config = config_module.load_approval_reminder_config()
        return int(args.func(args))
    except triage_gate.GateError as error:
        print(f"GATE-REFUSED {error}", file=sys.stderr)
        return error.exit_code
    except triage_llm.LlmCallError as error:
        print(f"LLM-FAIL {triage_core.redact(str(error))[:300]}", file=sys.stderr)
        return 3
    except triage_core.LlmParseError as error:
        print(f"LLM-PARSE-FAIL {triage_core.redact(str(error))[:300]}", file=sys.stderr)
        return 2
    except triage_core.AttachmentPolicyError as error:
        print(
            f"ATTACHMENT-REFUSED error_code={error.error_code} retryable=false {error}",
            file=sys.stderr,
        )
        return 2
    except FileNotFoundError as error:
        print(f"GATE-REFUSED 파일 없음: {error.filename}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

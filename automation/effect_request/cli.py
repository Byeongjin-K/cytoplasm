"""``request drive-save|command``, owner-command ``save-now``, and ``status``.

``request`` freezes what will run, posts ONE standard approval card in the request's own
thread and prints the ``APPROVAL-THREAD`` line the reply must cite. It never runs the
effect: the reaction watcher does that after the owner's ✅. Exit codes: 0 posted or
pending, 2 usage or invalid input, 3 the card could not be posted, 4 refused (owner-only
action or an effect with a dedicated path — the reason and the right command are printed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

from automation.interop.approval_lifecycle import Outcome

from automation.effect_request.gate import discord_runtime, owner_id_from_config, request_approval
from .model import (
    COMMAND,
    DRIVE_SAVE,
    LIVE_STATUSES,
    EffectRequest,
    EffectRequestError,
    EffectRequestStore,
    action_hash,
    file_sha256,
)
from .policy import refusal

EXIT_REFUSED: Final = 4
DEFAULT_KIND: Final = "doctype"
DEFAULT_TIMEOUT: Final = 600
MAX_TIMEOUT: Final = 3600


def _summary(raw: str) -> str:
    text = " ".join(unicodedata.normalize("NFC", raw).split())
    if not text:
        raise EffectRequestError("--summary 가 비어 있다 — 카드 제목이 된다")
    return text[:120]


def _drive_payload(args: argparse.Namespace, request_id: str, store: EffectRequestStore) -> dict[str, object]:
    from automation.drive_taxonomy import (
        TaxonomyError, artifact_name, category, ensure_depth, folder_parts, period_key,
    )

    source = Path(args.file).expanduser()
    if not source.is_file():
        raise EffectRequestError(f"파일 없음: {source}")
    try:
        selected = category(args.kind)
        if selected.gate_only or selected.skill_owned:
            raise EffectRequestError(f"kind={args.kind} 는 전용 경로로만 저장한다")
        on = date.fromisoformat(args.on) if args.on else date.today()
        title = unicodedata.normalize("NFC", args.title or source.stem).strip()
        parts = folder_parts(args.kind, on.year, project=args.project or None)
        drive_name = artifact_name(period_key(selected.periodicity, on), title, source.suffix)
        ensure_depth((*parts, drive_name))
    except (TaxonomyError, ValueError) as error:
        raise EffectRequestError(f"Drive 위치를 정할 수 없다: {error}") from error
    frozen = store.freeze_file(request_id, source)
    digest, size = file_sha256(frozen)
    return {
        "kind": args.kind, "title": title, "project": args.project or "", "on": on.isoformat(),
        "folder_parts": list(parts), "drive_name": drive_name, "frozen_path": str(frozen),
        "sha256": digest, "size": size, "sharing": "owner-only",
    }


def _command_payload(args: argparse.Namespace) -> dict[str, object]:
    argv = [str(item) for item in args.argv]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise EffectRequestError("실행할 명령이 없다 — `--` 뒤에 argv 를 준다")
    target = " ".join(args.target.split())
    if not target:
        raise EffectRequestError("--target(영향 대상)이 비어 있다")
    timeout = int(args.timeout)
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise EffectRequestError(f"--timeout 은 1~{MAX_TIMEOUT}초")
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise EffectRequestError(f"작업 폴더 없음: {cwd}")
    return {"argv": argv, "target": target, "cwd": str(cwd), "timeout": timeout}


def _new_record(args: argparse.Namespace, store: EffectRequestStore, *, reuse_live: bool = True) -> EffectRequest:
    summary = _summary(args.summary)
    effect = args.effect
    seed = hashlib.sha256(f"{effect}\0{summary}\0{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:16]
    payload = _drive_payload(args, seed, store) if effect == DRIVE_SAVE else _command_payload(args)
    digest = action_hash(effect, summary, payload)
    key_basis = json.dumps({"effect": effect, "summary": summary}, ensure_ascii=False, sort_keys=True)
    key = "effect:" + hashlib.sha256(key_basis.encode("utf-8")).hexdigest()[:24]
    for existing in store.all():
        if reuse_live and existing.key == key and existing.action_hash == digest and existing.status in LIVE_STATUSES:
            return existing
    record = EffectRequest(
        request_id=seed, key=key, effect=effect, summary=summary, payload=payload,
        action_hash=digest, created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        origin_channel_id=args.origin_channel_id, origin_message_id=args.origin_message_id,
    )
    store.create(record)
    return record


def _print_thread_line(record: EffectRequest) -> None:
    try:
        from automation.interop.thread_pointer import approval_thread_line
    except ImportError as error:
        print(f"APPROVAL-THREAD-UNAVAILABLE request={record.request_id} err={type(error).__name__}", file=sys.stderr)
        return
    print(approval_thread_line("request", record.request_id, {
        "approval_thread_id": record.approval_thread_id,
        "approval_guild_id": record.approval_guild_id,
    }))


def cmd_request(args: argparse.Namespace) -> int:
    from automation.notice_bot import resolve_token

    if args.effect == COMMAND:
        refused = refusal(args.summary + "\n" + args.target, tuple(str(item) for item in args.argv))
        if refused is not None:
            print(f"EFFECT-REQUEST-REFUSED code={refused.code} reason={refused.reason}", file=sys.stderr)
            return EXIT_REFUSED
    store = EffectRequestStore()
    record = _new_record(args, store)
    runtime = discord_runtime(resolve_token(), owner_id_from_config(), store)
    verdict = request_approval(record, store, runtime)
    if verdict.outcome not in {Outcome.POSTED, Outcome.PENDING}:
        reason = "unknown" if verdict.reason is None else verdict.reason.value
        print(f"EFFECT-REQUEST-NOT-POSTED request={record.request_id} reason={reason}", file=sys.stderr)
        return 3
    current = store.get(record.request_id) or record
    print(f"REQUESTED request={current.request_id} effect={current.effect} hash={current.action_hash}")
    _print_thread_line(current)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    chosen = [r for r in EffectRequestStore().all() if not args.request or r.request_id == args.request]
    for record in sorted(chosen, key=lambda item: item.created_at):
        print(
            f"EFFECT-REQUEST request={record.request_id} effect={record.effect} status={record.status} "
            f"attempts={record.attempts} error={record.last_error or '-'}"
        )
    return 0 if chosen else 1


def cmd_save_now(args: argparse.Namespace) -> int:
    """Save from a gate-verified owner command; no Discord approval or watcher retry."""
    from automation.drive_outputs import publish

    from .execute import execute
    from .render import drive_location

    refused = refusal(args.summary)
    if refused is not None:
        print(f"EFFECT-REQUEST-REFUSED code={refused.code} reason={refused.reason}", file=sys.stderr)
        return EXIT_REFUSED
    store = EffectRequestStore()
    record = _new_record(args, store, reuse_live=False)
    try:
        receipt = execute(record, publisher=publish)
    except EffectRequestError as error:
        store.update(replace(
            record, status="failed", attempts=1, last_error=str(error)[:300],
            finished_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))
        raise
    receipt.update(
        method="owner_command",
        origin_channel_id=record.origin_channel_id,
        origin_message_id=record.origin_message_id,
    )
    store.update(replace(
        record, status="done", attempts=1, receipt=receipt,
        finished_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    ))
    links = receipt["links"]
    location = str(links[0]) if links else drive_location(record.payload)
    print(f"OWNER-COMMAND-WRITTEN drive-save {location}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m automation.effect_request")
    commands = parser.add_subparsers(dest="command", required=True)
    request = commands.add_parser("request", help="post one approval card for an effect no skill owns")
    effects = request.add_subparsers(dest="effect", required=True)
    for name, helptext in ((DRIVE_SAVE, "save one file to Drive, owner-only"),
                           (COMMAND, "run one command after approval"),
                           ("save-now", "save from a gate-verified owner command without a card")):
        immediate = name == "save-now"
        sub = (commands if immediate else effects).add_parser(name, help=helptext)
        sub.add_argument("--summary", required=True, help="한 줄 설명 — 카드·스레드 제목")
        sub.add_argument("--origin-channel-id", default="", required=immediate)
        sub.add_argument("--origin-message-id", default="", required=immediate)
        sub.set_defaults(handler=cmd_save_now if immediate else cmd_request)
        if immediate:
            sub.set_defaults(effect=DRIVE_SAVE)
        if name == DRIVE_SAVE or immediate:
            sub.add_argument("--file", required=True)
            sub.add_argument("--title", default=None, help="Drive 파일 제목(기본: 파일 이름)")
            sub.add_argument("--kind", default=DEFAULT_KIND, help="Drive 카테고리(기본 doctype=문서)")
            sub.add_argument("--project", default="", help="과제 폴더 한 단(선택)")
            sub.add_argument("--on", default=None, help="기준 날짜 YYYY-MM-DD(기본 오늘)")
        else:
            sub.add_argument("--target", required=True, help="영향을 받는 대상(사람·서비스·위치)")
            sub.add_argument("--cwd", default=str(Path.home()))
            sub.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
            sub.add_argument("argv", nargs=argparse.REMAINDER, help="-- 뒤에 실행할 명령")
    status = commands.add_parser("status", help="show effect requests and their receipts")
    status.add_argument("--request", default="")
    status.set_defaults(handler=cmd_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (EffectRequestError, OSError, UnicodeDecodeError) as error:
        print(f"EFFECT-REQUEST-ERROR {error}", file=sys.stderr)
        return 2

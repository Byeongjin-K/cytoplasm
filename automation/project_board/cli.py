"""``python3 -m automation.project_board`` — analysis, review loop, progress, dashboard.

Exit codes: 0 done (or nothing to do), 2 usage or invalid input, 3 a card could not be
posted (reason printed), 4 the model or a source was unavailable (retry later).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from automation.interop.approval_lifecycle import Outcome
from automation.project_board.proposal_gate import discord_runtime, owner_id_from_config, request_approval

from . import sources
from .config import load_config
from .llm import describe, provider_from_config
from .models import STATUSES, ProjectBoardError
from .proposals import ProposalRecord
from .service import (
    Board,
    Revision,
    digest_mail_argv,
    digest_snapshot,
    parse_todo,
    pending_views,
    propose,
    rebuild_dashboard,
    request_digest_mail,
    revise,
)

KST = ZoneInfo("Asia/Seoul")


class CardRefused(ProjectBoardError):
    pass


def today_kst() -> date:
    return datetime.now(KST).date()


def _print_thread_line(record: ProposalRecord) -> None:
    try:
        from automation.interop.thread_pointer import approval_thread_line
    except ImportError as error:
        print(f"APPROVAL-THREAD-UNAVAILABLE proposal={record.request_id} err={type(error).__name__}", file=sys.stderr)
        return
    print(approval_thread_line("proposal", record.request_id, {
        "approval_thread_id": record.approval_thread_id,
        "approval_guild_id": record.approval_guild_id,
    }))


def post_card(board: Board, record: ProposalRecord) -> ProposalRecord:
    """Post (or keep pending) the one approval card of this proposal revision."""
    runtime = discord_runtime(os.environ.get("DISCORD_BOT_TOKEN", ""), owner_id_from_config(), board.proposals)
    verdict = request_approval(record, board.proposals, runtime)
    if verdict.outcome not in {Outcome.POSTED, Outcome.PENDING}:
        reason = "unknown" if verdict.reason is None else verdict.reason.value
        raise CardRefused(f"proposal card refused: {reason}")
    current = board.proposals.get(record.request_id) or record
    _print_thread_line(current)
    return current


def _document(args: argparse.Namespace) -> sources.SourceDocument:
    if args.meeting:
        return sources.read_meeting(Path(args.meeting).expanduser())
    document = sources.read_mail(sources.mail_runtime(), args.mail)
    if document is None:
        raise ProjectBoardError(f"institution mail {args.mail} has no stored document")
    return document


def cmd_analyze(args: argparse.Namespace) -> int:
    board = Board.default()
    config = load_config()
    provider = provider_from_config(config.llm)
    poster = None if args.no_post else (lambda record: post_card(board, record))
    record, outcome = propose(
        board, _document(args), provider, today=today_kst(), post=poster, config=config,
        origin_channel_id=args.origin_channel_id, origin_message_id=args.origin_message_id,
    )
    if record is None:
        print(f"PROPOSAL-SKIP reason={outcome}")
        return 0
    print(
        f"PROPOSAL {outcome} proposal={record.request_id} project={record.project} "
        f"todos={record.todo_count} status={record.status} hash={record.action_hash}"
    )
    return 0


def _pairs(values: Sequence[str], what: str) -> tuple[tuple[int, str], ...]:
    pairs = []
    for value in values:
        number, sep, text = value.partition("=")
        if not sep or not number.strip().isdigit():
            raise ProjectBoardError(f"{what} expects N=value, got {value!r}")
        pairs.append((int(number), text.strip()))
    return tuple(pairs)


def cmd_revise(args: argparse.Namespace) -> int:
    board = Board.default()
    change = Revision(
        project=args.project, project_owner=args.project_owner, summary=args.summary,
        replace_todos=tuple(parse_todo(t) for t in args.todo) if args.todo else None,
        add=tuple(parse_todo(t) for t in args.add), drop=tuple(args.drop),
        set_due=_pairs(args.set_due, "--set-due"), set_owner=_pairs(args.set_owner, "--set-owner"),
        set_title=_pairs(args.set_title, "--set-title"),
    )
    poster = None if args.no_post else (lambda record: post_card(board, record))
    record = revise(board, args.proposal, change, post=poster)
    print(
        f"PROPOSAL revised proposal={record.request_id} revision={record.revision} "
        f"replaces={record.previous_request_id} todos={record.todo_count} status={record.status}"
    )
    return 0


def cmd_proposals(args: argparse.Namespace) -> int:
    del args
    for record in sorted(Board.default().proposals.all(), key=lambda r: r.created_at):
        print(
            f"PROPOSAL proposal={record.request_id} status={record.status} rev={record.revision} "
            f"project={record.project} todos={record.todo_count} source={record.source_kind}:{record.source_id} "
            f"error={record.last_error or '-'}"
        )
    return 0


def cmd_progress(args: argparse.Namespace) -> int:
    board = Board.default()
    todo = board.store.record_progress(args.todo, status=args.status, progress=args.progress, note=args.note)
    path = rebuild_dashboard(board, today=today_kst())
    print(f"PROGRESS todo={todo.todo_id} status={todo.status} progress={todo.progress} dashboard={path}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    del args
    print(f"DASHBOARD {rebuild_dashboard(Board.default(), today=today_kst())}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import make_server

    board = Board.default()
    config = load_config()
    runtime = sources.mail_runtime()
    meetings = sources.meeting_dir()

    def read_source(kind: str, source_id: str) -> str | None:
        if kind == "mail":
            return sources.mail_markdown(runtime, source_id)
        if kind == "meeting":
            return sources.meeting_text(meetings, source_id)
        return None

    def attachment(kind: str, source_id: str, name: str) -> Path | None:
        return sources.mail_attachment(runtime, source_id, name) if kind == "mail" else None

    port = args.port or config.port
    server = make_server(
        host=args.host, port=port, token=board.token(), store=board.store, today=today_kst,
        pending=lambda: pending_views(board), read_source=read_source, attachment_path=attachment,
        on_change=lambda: rebuild_dashboard(board, today=today_kst()),
    )
    print(f"DASHBOARD-SERVE http://{args.host}:{server.server_address[1]}/ (token: {board.root / 'serve.token'})")
    sys.stdout.flush()
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    board = Board.default()
    config = load_config()
    path = digest_snapshot(board, today=today_kst())
    print(f"DIGEST {path}")
    if not args.request_mail:
        return 0
    from automation.skill_mount import live_root

    mail_cli = live_root() / "mail" / "scripts" / "triage_cli.py"
    body = (
        f"{today_kst().isoformat()} 기준 프로젝트 현황 대시보드를 첨부합니다.\n"
        "첨부 HTML 파일을 브라우저로 열어 보세요(외부 연결 없이 열립니다)."
    )
    recipients = tuple(args.to) or config.digest_to
    argv = digest_mail_argv(mail_cli, recipients, config.digest_subject, path, body)
    print(request_digest_mail(argv).rstrip())
    return 0


def cmd_dryrun(args: argparse.Namespace) -> int:
    from . import dryrun

    config = load_config()
    board = Board.default()
    summary = dryrun.run(
        sources.mail_runtime(), provider_from_config(config.llm), config, today=today_kst(), days=args.days,
        limit=args.limit, out=Path(args.out).expanduser(), version=args.prompt,
        stored_projects=board.store.project_names() if board.store.path.exists() else (), workers=args.workers,
        sequential=args.sequential,
    )
    print(
        f"DRYRUN total={summary.total} same_thread={summary.same_thread} relevant={summary.relevant} unrelated={summary.unrelated} "
        f"errors={summary.errors} todos={summary.todos} out={summary.out}"
    )
    for name, count in sorted(summary.projects.items(), key=lambda item: -item[1]):
        print(f"DRYRUN-PROJECT {count} {name}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    del args
    board = Board.default()
    config = load_config()
    records = board.proposals.all()
    counts: dict[str, int] = {}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1
    print(f"MODEL {describe(config.llm)}")
    print(f"ANALYZE enabled={str(config.analyze_enabled).lower()} max_per_tick={config.max_per_tick}")
    print("PROPOSALS " + (" ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"))
    print(f"PROJECTS {len(board.store.project_names())} dashboard={board.dashboard_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m automation.project_board")
    commands = parser.add_subparsers(dest="command", required=True)

    analyze = commands.add_parser("analyze", help="analyse one mail or minutes and post a proposal card")
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--mail", help="institution mail uid (MailOn)")
    source.add_argument("--meeting", help="finished minutes markdown path")
    analyze.add_argument("--no-post", action="store_true", help="store the proposal without posting its card")
    analyze.add_argument("--origin-channel-id", default="")
    analyze.add_argument("--origin-message-id", default="")
    analyze.set_defaults(handler=cmd_analyze)

    rev = commands.add_parser("revise", help="post a revised proposal (owner's edits) in the same thread")
    rev.add_argument("proposal")
    rev.add_argument("--project")
    rev.add_argument("--project-owner")
    rev.add_argument("--summary")
    rev.add_argument("--todo", action="append", default=[], help="replace all todos: '제목|담당|YYYY-MM-DD'")
    rev.add_argument("--add", action="append", default=[], help="add a todo: '제목|담당|YYYY-MM-DD'")
    rev.add_argument("--drop", action="append", type=int, default=[], help="drop todo number N")
    rev.add_argument("--set-due", action="append", default=[], help="N=YYYY-MM-DD (empty = 미정)")
    rev.add_argument("--set-owner", action="append", default=[], help="N=담당")
    rev.add_argument("--set-title", action="append", default=[], help="N=제목")
    rev.add_argument("--no-post", action="store_true")
    rev.set_defaults(handler=cmd_revise)

    commands.add_parser("proposals", help="list proposals").set_defaults(handler=cmd_proposals)

    progress = commands.add_parser("progress", help="record progress or completion of an approved todo")
    progress.add_argument("todo")
    progress.add_argument("--status", choices=STATUSES)
    progress.add_argument("--progress", type=int)
    progress.add_argument("--note")
    progress.set_defaults(handler=cmd_progress)

    commands.add_parser("build", help="rebuild the dashboard file").set_defaults(handler=cmd_build)

    serve = commands.add_parser("serve", help="serve the dashboard on loopback")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    serve.set_defaults(handler=cmd_serve)

    digest = commands.add_parser("digest", help="dated dashboard snapshot; --request-mail posts a mail card")
    digest.add_argument("--request-mail", action="store_true")
    digest.add_argument("--to", action="append", default=[])
    digest.set_defaults(handler=cmd_digest)

    dry = commands.add_parser("dryrun", help="read-only analysis of recent real mail (no card, no data, no dashboard)")
    dry.add_argument("--days", type=int, default=28)
    dry.add_argument("--limit", type=int, default=60)
    dry.add_argument("--out", required=True, help="private JSONL path outside any checkout")
    dry.add_argument("--prompt", choices=("v1", "v2"), default=None)
    dry.add_argument("--workers", type=int, default=3)
    dry.add_argument("--sequential", action="store_true", help="oldest first, earlier todos count as already open")
    dry.set_defaults(handler=cmd_dryrun)

    commands.add_parser("status", help="model, analysis switch, proposal counts").set_defaults(handler=cmd_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except CardRefused as error:
        print(f"PROPOSAL-CARD-REFUSED {error}", file=sys.stderr)
        return 3
    except ProjectBoardError as error:
        print(f"PROJECT-BOARD-ERROR {error}", file=sys.stderr)
        return 4 if error.retryable else 2
    except (OSError, UnicodeDecodeError) as error:
        print(f"PROJECT-BOARD-ERROR {error}", file=sys.stderr)
        return 2

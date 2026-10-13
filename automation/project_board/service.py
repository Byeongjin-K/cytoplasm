"""Project-board operations shared by the CLI and the no-agent crons.

Analysis never writes project data: it only creates a proposal and asks for its card.
The first analysis tick sets the mail watermark to "now" instead of proposing from the
whole mailbox history — the owner approves new mail, not years of backlog.
"""

from __future__ import annotations

import json
import secrets
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

from .analysis import analyze
from .board_store import BoardStore, utc_stamp
from .config import BoardConfig, state_root
from .dashboard import render_dashboard
from .llm import LlmProvider
from .models import PendingView, ProjectBoardError, Proposal, ProposedTodo, proposal_from_dict
from .proposals import REVISABLE_STATUSES, ProposalRecord, ProposalStore, atomic_write, new_record
from .sources import MailRuntime, SourceDocument, latest_saved_at, new_mail, read_mail

Poster = Callable[[ProposalRecord], object]
DASHBOARD_FILE: Final = "dashboard.html"


@dataclass(frozen=True, slots=True)
class Board:
    root: Path

    @classmethod
    def default(cls) -> Board:
        return cls(state_root())

    @property
    def proposals(self) -> ProposalStore:
        return ProposalStore(self.root)

    @property
    def store(self) -> BoardStore:
        return BoardStore(self.root / "board.db")

    @property
    def dashboard_path(self) -> Path:
        return self.root / "dist" / DASHBOARD_FILE

    def token(self) -> str:
        path = self.root / "serve.token"
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            value = ""
        if len(value) < 32:
            value = secrets.token_urlsafe(32)
            atomic_write(path, value)
        return value


def pending_views(board: Board) -> tuple[PendingView, ...]:
    from automation.interop.owner_message import LinkStatus, discord_link

    views = []
    for record in board.proposals.all():
        if record.status not in {"proposed", "posted", "approved"}:
            continue
        link = discord_link(
            space="guild", channel_id=record.approval_thread_id or None, guild_id=record.approval_guild_id or None
        )
        views.append(
            PendingView(
                record.request_id, record.project, record.todo_count, record.source_subject,
                record.created_at, link.url if link.status is LinkStatus.AVAILABLE and link.url else "",
            )
        )
    return tuple(sorted(views, key=lambda v: v.created_at))


def rebuild_dashboard(board: Board, *, today: date, now: datetime | None = None) -> Path:
    snapshot = board.store.snapshot(today=today, pending=pending_views(board), now=now)
    atomic_write(board.dashboard_path, render_dashboard(snapshot, mode="file"))
    return board.dashboard_path


def propose(
    board: Board,
    document: SourceDocument,
    provider: LlmProvider,
    *,
    today: date,
    post: Poster | None,
    origin_channel_id: str = "",
    origin_message_id: str = "",
    now: datetime | None = None,
) -> tuple[ProposalRecord | None, str]:
    """Analyse one source and (unless it is unrelated or already proposed) request its card."""
    existing = board.proposals.live_for_source(document.ref.kind, document.ref.source_id)
    if existing is not None:
        return existing, f"already-proposed {existing.request_id} status={existing.status}"
    proposal = analyze(document, provider, known_projects=board.store.project_names(), today=today)
    if proposal is None:
        return None, "unrelated"
    record = new_record(
        proposal, created_at=utc_stamp(now), origin_channel_id=origin_channel_id,
        origin_message_id=origin_message_id,
    )
    board.proposals.create(record, proposal)
    if post is not None:
        post(record)
    return board.proposals.get(record.request_id) or record, "proposed"


def _watermark_path(board: Board) -> Path:
    return board.root / "analyze-state.json"


def read_watermark(board: Board) -> int | None:
    try:
        return int(json.loads(_watermark_path(board).read_text(encoding="utf-8"))["mail_saved_at"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProjectBoardError("analysis watermark is unreadable") from error


def write_watermark(board: Board, saved_at: int) -> None:
    atomic_write(_watermark_path(board), json.dumps({"mail_saved_at": saved_at}))


@dataclass(frozen=True, slots=True)
class AnalyzeTick:
    proposed: tuple[str, ...] = ()
    unrelated: int = 0
    skipped: int = 0
    deferred: str = ""
    initialised: bool = False


def analyze_new_mail(
    board: Board,
    runtime: MailRuntime,
    provider: LlmProvider,
    config: BoardConfig,
    *,
    today: date,
    post: Poster | None,
) -> AnalyzeTick:
    """New inbox mail since the watermark → proposals; a retryable failure stops the tick there."""
    watermark = read_watermark(board)
    if watermark is None:
        write_watermark(board, latest_saved_at(runtime))
        return AnalyzeTick(initialised=True)
    proposed: list[str] = []
    unrelated = skipped = 0
    for uid, saved_at in new_mail(runtime, after_saved_at=watermark, limit=config.max_per_tick):
        document = read_mail(runtime, uid)
        if document is None:
            skipped += 1
            write_watermark(board, saved_at)
            continue
        try:
            record, outcome = propose(board, document, provider, today=today, post=post)
        except ProjectBoardError as error:
            if error.retryable:
                return AnalyzeTick(tuple(proposed), unrelated, skipped, deferred=f"{uid}: {error}")
            skipped += 1
            write_watermark(board, saved_at)
            continue
        if outcome == "proposed" and record is not None:
            proposed.append(record.request_id)
        elif outcome == "unrelated":
            unrelated += 1
        else:
            skipped += 1
        write_watermark(board, saved_at)
    return AnalyzeTick(tuple(proposed), unrelated, skipped)


@dataclass(frozen=True, slots=True)
class Revision:
    project: str | None = None
    project_owner: str | None = None
    summary: str | None = None
    replace_todos: tuple[ProposedTodo, ...] | None = None
    add: tuple[ProposedTodo, ...] = ()
    drop: tuple[int, ...] = ()
    set_due: tuple[tuple[int, str], ...] = ()
    set_owner: tuple[tuple[int, str], ...] = ()
    set_title: tuple[tuple[int, str], ...] = ()


def revised_proposal(proposal: Proposal, change: Revision) -> Proposal:
    todos = list(change.replace_todos if change.replace_todos is not None else proposal.todos)
    count = len(todos)
    for index in (*change.drop, *(i for i, _ in (*change.set_due, *change.set_owner, *change.set_title))):
        if not 1 <= index <= count:
            raise ProjectBoardError(f"todo number {index} is out of range 1..{count}")
    for index, value in change.set_due:
        todos[index - 1] = replace(todos[index - 1], due=value or None, due_reason="소유자 수정")
    for index, value in change.set_owner:
        todos[index - 1] = replace(todos[index - 1], owner=value)
    for index, value in change.set_title:
        todos[index - 1] = replace(todos[index - 1], title=value)
    kept = [todo for number, todo in enumerate(todos, start=1) if number not in set(change.drop)]
    raw = proposal.to_dict()
    raw["todos"] = [
        {"title": t.title, "owner": t.owner, "due": t.due, "due_reason": t.due_reason}
        for t in (*kept, *change.add)
    ]
    for name in ("project", "project_owner", "summary"):
        value = getattr(change, name)
        if value is not None:
            raw[name] = value
    return proposal_from_dict(raw)


def revise(
    board: Board, request_id: str, change: Revision, *, post: Poster | None, now: datetime | None = None
) -> ProposalRecord:
    """A new revision under the same key; the façade supersedes the old pending card."""
    current = board.proposals.get(request_id)
    if current is None:
        raise ProjectBoardError(f"no proposal {request_id}")
    if current.status not in REVISABLE_STATUSES:
        raise ProjectBoardError(
            f"proposal {request_id} is {current.status}; an approved proposal is not revised — make a new one"
        )
    proposal = revised_proposal(board.proposals.frozen(current), change)
    record = new_record(
        proposal, created_at=utc_stamp(now), revision=current.revision + 1, previous_request_id=current.request_id,
        origin_channel_id=current.origin_channel_id, origin_message_id=current.origin_message_id,
    )
    if record.action_hash == current.action_hash:
        raise ProjectBoardError("the revision changes nothing")
    board.proposals.create(record, proposal)
    if post is not None:
        post(record)
    return board.proposals.get(record.request_id) or record


def parse_todo(text: str) -> ProposedTodo:
    """``제목|담당|YYYY-MM-DD`` (담당·기한 생략 가능)."""
    title, _, rest = text.partition("|")
    owner, _, due = rest.partition("|")
    raw = proposal_from_dict(
        {
            "project": "x", "summary": "x",
            "todos": [{"title": title, "owner": owner, "due": due.strip() or None, "due_reason": "소유자 지정"}],
            "source": {"kind": "mail", "source_id": "x"},
        }
    )
    return raw.todos[0]


def digest_snapshot(board: Board, *, today: date, now: datetime | None = None) -> Path:
    """A dated, never-overwritten copy — the mail approval binds the attachment's bytes."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    path = board.root / "digests" / f"project-dashboard-{today.isoformat()}-{stamp}.html"
    snapshot = board.store.snapshot(today=today, pending=pending_views(board), now=now)
    atomic_write(path, render_dashboard(snapshot, mode="file"))
    return path


def digest_mail_argv(
    mail_cli: Path, recipients: Sequence[str], subject: str, attachment: Path, body: str
) -> list[str]:
    if not recipients:
        raise ProjectBoardError("digest has no recipients (config digest.to)")
    argv = ["python3", str(mail_cli), "compose", "--to", recipients[0]]
    for extra in recipients[1:]:
        argv += ["--cc", extra]
    return [*argv, "--subject", subject, "--body", body, "--attachment", str(attachment)]


def request_digest_mail(argv: list[str], *, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> str:
    """Hand the snapshot to the mail skill's compose — its approval card is the only way out."""
    completed = runner(argv, capture_output=True, text=True, check=False, timeout=300)
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-1:] or [""]
        raise ProjectBoardError(f"mail compose refused (rc={completed.returncode}): {tail[0][:200]}")
    return completed.stdout

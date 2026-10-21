"""Read-only analysis over real collected mail, for checking classification before going live.

Nothing here writes proposals, the project data, a card or the dashboard, and the mailbox
is only read. The one output is a private JSONL (directory 0700, file 0600) the operator
judges by hand; it is refused inside a git checkout so real mail never lands in a repo.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .analysis import _json_object, build_prompt, parse_answer
from .config import BoardConfig
from .llm import LlmProvider
from .models import ProjectBoardError
from . import minutes
from .sources import MailRuntime, SourceDocument, meeting_key, read_mail, recent_mail, thread_key


@dataclass(frozen=True, slots=True)
class DryRunSummary:
    total: int
    same_thread: int
    relevant: int
    unrelated: int
    errors: int
    todos: int
    projects: dict[str, int]
    out: Path


def _inside_checkout(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (path, *path.parents))


def _one(
    runtime: MailRuntime, uid: str, provider: LlmProvider, catalog: tuple[str, ...], today: date, version: str,
    owner: tuple[str, ...],
    team: tuple[str, ...] = (),
    open_todos: tuple[str, ...] = (),
) -> dict[str, object]:
    document = read_mail(runtime, uid)
    if document is None:
        return {"uid": uid, "error": "no stored document"}
    return _analyse(document, uid, provider, catalog, today, version, owner, team, open_todos)


def _analyse(
    document: SourceDocument, uid: str, provider: LlmProvider, catalog: tuple[str, ...], today: date, version: str,
    owner: tuple[str, ...], team: tuple[str, ...], open_todos: tuple[str, ...],
) -> dict[str, object]:
    row: dict[str, object] = {
        "uid": uid, "subject": document.ref.subject, "sender": document.ref.sender, "date": document.ref.date,
        "attachments": list(document.ref.attachments),
    }
    started = time.monotonic()
    try:
        answer = provider.complete(
            build_prompt(document, catalog, today, version=version, owner=owner, team=team, open_todos=open_todos)
        )
        proposal = parse_answer(answer.text, document.ref, provider=answer.provider, model=answer.model)
    except ProjectBoardError as error:
        row.update(error=str(error)[:300])
    else:
        if proposal is None:
            row.update(relevant=False, reason=str(_json_object(answer.text).get("reason") or ""))
        else:
            row.update(
                relevant=True, project=proposal.project, project_owner=proposal.project_owner,
                summary=proposal.summary, model=f"{proposal.provider}/{proposal.model}",
                todos=[{"title": t.title, "owner": t.owner, "due": t.due, "due_reason": t.due_reason, "time": t.time, "place": t.place}
                       for t in proposal.todos],
            )
    row["seconds"] = round(time.monotonic() - started, 1)
    return row


def _write(out: Path, rows: list[dict[str, object]], chosen: str, duplicates: int) -> DryRunSummary:
    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({**row, "prompt": chosen}, ensure_ascii=False) + "\n")
    projects: dict[str, int] = {}
    for row in rows:
        if row.get("relevant"):
            name = str(row.get("project"))
            projects[name] = projects.get(name, 0) + 1
    return DryRunSummary(
        total=len(rows), same_thread=duplicates,
        relevant=sum(1 for r in rows if r.get("relevant") is True),
        unrelated=sum(1 for r in rows if r.get("relevant") is False),
        errors=sum(1 for r in rows if "error" in r),
        todos=sum(len(r.get("todos") or []) for r in rows),  # type: ignore[arg-type]
        projects=projects, out=out,
    )


def run_minutes(
    drive: minutes.Drive,
    provider: LlmProvider,
    config: BoardConfig,
    *,
    today: date,
    limit: int,
    out: Path,
    version: str | None = None,
    stored_projects: Sequence[str] = (),
) -> DryRunSummary:
    """The N newest finalized minutes, analysed oldest first; earlier todos count as already open."""
    from automation.drive_taxonomy import category_parts

    if _inside_checkout(out.parent.resolve()):
        raise ProjectBoardError("dry-run output must stay outside any git checkout")
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    catalog = config.catalog_lines(tuple(stored_projects))
    chosen = version or config.prompt_version
    rows: list[dict[str, object]] = []
    seen: list[str] = []
    meetings: dict[str, str] = {}
    duplicates = 0
    with tempfile.TemporaryDirectory(prefix="pb-dry-minutes-") as cache:
        for item in minutes.scan(drive, category_parts("meeting"))[-limit:] if limit > 0 else ():
            try:
                document = minutes.fetch(drive, item, Path(cache))
            except Exception as error:  # noqa: BLE001 - one unreadable file is a row, not a stopped run
                rows.append({"uid": item.source_id, "error": type(error).__name__})
                continue
            series = meeting_key(document.ref.subject)
            if series in meetings:
                duplicates += 1
            meetings.setdefault(series, item.source_id)
            row = _analyse(document, item.source_id, provider, catalog, today, chosen, config.owner_names,
                           config.team_names, tuple(seen[-40:]))
            row["series"] = meetings[series]
            seen += [f'{row.get("project")} · {todo["title"]}' for todo in row.get("todos") or []]  # type: ignore[union-attr]
            rows.append(row)
    return _write(out, rows, chosen, duplicates)


def run(
    runtime: MailRuntime,
    provider: LlmProvider,
    config: BoardConfig,
    *,
    today: date,
    days: int,
    limit: int,
    out: Path,
    version: str | None = None,
    stored_projects: Sequence[str] = (),
    workers: int = 3,
    sequential: bool = False,
) -> DryRunSummary:
    if _inside_checkout(out.parent.resolve()):
        raise ProjectBoardError("dry-run output must stay outside any git checkout")
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    uids = recent_mail(runtime, since=(today - timedelta(days=days)).isoformat(), limit=limit)
    catalog = config.catalog_lines(tuple(stored_projects))
    chosen = version or config.prompt_version
    newest: dict[str, str] = {}
    duplicates: list[dict[str, object]] = []
    for uid in uids:
        document = read_mail(runtime, uid)
        thread = thread_key(document.ref.subject) if document is not None and document.ref.subject else uid
        if thread in newest:
            duplicates.append({"uid": uid, "relevant": None, "duplicate_of": newest[thread],
                               "subject": document.ref.subject if document else ""})
        else:
            newest[thread] = uid
    if sequential:
        rows = []
        seen: list[str] = []
        for uid in reversed(list(newest.values())):
            row = _one(runtime, uid, provider, catalog, today, chosen, config.owner_names, config.team_names,
                       tuple(seen[-40:]))
            seen += [f'{row.get("project")} · {todo["title"]}' for todo in row.get("todos") or []]  # type: ignore[union-attr]
            rows.append(row)
    else:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            rows = list(pool.map(
                lambda uid: _one(runtime, uid, provider, catalog, today, chosen, config.owner_names,
                                 config.team_names), newest.values()
            ))
    rows += duplicates
    return _write(out, rows, chosen, len(duplicates))

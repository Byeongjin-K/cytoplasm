"""Shapes shared by every project-board stage: a proposal, its source, and the board view.

A proposal is what the analysis suggests and what the owner approves as one unit. Its
canonical JSON is the approval preimage, so a single changed character (a due date, an
owner, a dropped todo) is a different approval. The board view is what the dashboard
renders; it is read from the approved store only — nothing unapproved reaches it except
the explicit "검토 대기" list of pending proposals.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Final

STATUSES: Final = ("열림", "진행", "완료", "보류")
OPEN_STATUSES: Final = frozenset({"열림", "진행", "보류"})
SOURCE_KINDS: Final = frozenset({"mail", "meeting"})
MAX_TODOS: Final = 12
MAX_TITLE: Final = 200
MAX_PROJECT: Final = 60
MAX_SUMMARY: Final = 600
GRADES: Final = ("공개", "과제내부", "대외비", "기관지정")
DEFAULT_GRADE: Final = "과제내부"
NEVER_EXPORTED: Final = frozenset({"대외비", "기관지정"})


def grade_rank(grade: str) -> int:
    if grade not in GRADES:
        raise ProjectBoardError(f"grade must be one of {', '.join(GRADES)}")
    return GRADES.index(grade)


def stricter(*grades: str) -> str:
    """The strictest of the given grades (empty values are ignored; none at all → the default)."""
    present = [grade for grade in grades if grade]
    return max(present, key=grade_rank) if present else DEFAULT_GRADE


class ProjectBoardError(RuntimeError):
    """A refused or failed project-board step; ``retryable`` tells a watcher whether to try again."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


_CLOCK: Final = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def _clock(value: object) -> str:
    """An optional HH:MM; anything else (a range, prose, a wrong hour) is dropped rather than refused."""
    return value.strip() if isinstance(value, str) and _CLOCK.match(value.strip()) else ""


def _text(value: object, limit: int, what: str, *, required: bool = True) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ProjectBoardError(f"{what} must not be empty")
    if len(text) > limit:
        raise ProjectBoardError(f"{what} is longer than {limit} characters")
    return text


def valid_due(value: object) -> str | None:
    """``YYYY-MM-DD`` or ``None`` (미정). Anything else is refused, never guessed."""
    if value in (None, "", "null", "미정"):
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise ProjectBoardError(f"due date must be YYYY-MM-DD: {text!r}") from error


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where a proposal came from — the v1 record it can link back to."""

    kind: str
    source_id: str
    subject: str
    sender: str = ""
    date: str = ""
    attachments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise ProjectBoardError(f"unknown source kind: {self.kind}")
        if not self.source_id:
            raise ProjectBoardError("source id must not be empty")


@dataclass(frozen=True, slots=True)
class ProposedTodo:
    title: str
    owner: str = ""
    due: str | None = None
    due_reason: str = ""
    time: str = ""
    place: str = ""


@dataclass(frozen=True, slots=True)
class TodoExtra:
    """What the approved proposal said about a todo beyond the board row: why that due date, at what time, where."""

    due_reason: str = ""
    time: str = ""
    place: str = ""


@dataclass(frozen=True, slots=True)
class Proposal:
    project: str
    summary: str
    todos: tuple[ProposedTodo, ...]
    source: SourceRef
    project_owner: str = ""
    provider: str = ""
    model: str = ""
    grade: str = ""

    def to_dict(self) -> dict[str, object]:
        raw: dict[str, object] = {
            "project": self.project,
            "project_owner": self.project_owner,
            "summary": self.summary,
            "todos": [
                {"title": t.title, "owner": t.owner, "due": t.due, "due_reason": t.due_reason, "time": t.time, "place": t.place}
                for t in self.todos
            ],
            "source": {
                "kind": self.source.kind,
                "source_id": self.source.source_id,
                "subject": self.source.subject,
                "sender": self.source.sender,
                "date": self.source.date,
                "attachments": list(self.source.attachments),
            },
            "provider": self.provider,
            "model": self.model,
        }
        if self.grade:
            raw["grade"] = self.grade  # absent for ungraded proposals, so their approval hash is unchanged
        return raw


def proposal_from_dict(raw: dict[str, object]) -> Proposal:
    """Validate and normalise a proposal (from the model, a revision or the store)."""
    source = raw.get("source")
    todos = raw.get("todos")
    if not isinstance(source, dict) or not isinstance(todos, list):
        raise ProjectBoardError("proposal needs a source object and a todos list")
    if len(todos) > MAX_TODOS:
        raise ProjectBoardError(f"a proposal holds at most {MAX_TODOS} todos")
    items: list[ProposedTodo] = []
    for entry in todos:
        if not isinstance(entry, dict):
            raise ProjectBoardError("each todo must be an object")
        items.append(
            ProposedTodo(
                title=_text(entry.get("title"), MAX_TITLE, "todo title"),
                owner=_text(entry.get("owner"), 60, "todo owner", required=False),
                due=valid_due(entry.get("due")),
                due_reason=_text(entry.get("due_reason"), 200, "due reason", required=False),
                time=_clock(entry.get("time")),
                place=_text(entry.get("place"), 80, "todo place", required=False),
            )
        )
    attachments = source.get("attachments") or []
    if not isinstance(attachments, list):
        raise ProjectBoardError("source attachments must be a list")
    return Proposal(
        project=_text(raw.get("project"), MAX_PROJECT, "project"),
        project_owner=_text(raw.get("project_owner"), 60, "project owner", required=False),
        summary=_text(raw.get("summary"), MAX_SUMMARY, "summary"),
        todos=tuple(items),
        source=SourceRef(
            kind=str(source.get("kind") or ""),
            source_id=str(source.get("source_id") or ""),
            subject=_text(source.get("subject"), 300, "source subject", required=False),
            sender=_text(source.get("sender"), 200, "source sender", required=False),
            date=str(source.get("date") or ""),
            attachments=tuple(str(name) for name in attachments),
        ),
        provider=str(raw.get("provider") or ""),
        model=str(raw.get("model") or ""),
        grade=_grade(raw.get("grade")),
    )


def _grade(value: object) -> str:
    if value in (None, ""):
        return ""
    grade = str(value).strip()
    grade_rank(grade)
    return grade


def canonical_json(proposal: Proposal) -> str:
    return json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def proposal_hash(proposal: Proposal) -> str:
    """The approval binding: sha256 of the canonical proposal (hex, 64)."""
    return hashlib.sha256(canonical_json(proposal).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TodoRow:
    todo_id: str
    proposal_id: str
    project: str
    title: str
    owner: str
    due: str | None
    status: str
    progress: int
    source_kind: str
    source_id: str
    source_subject: str
    approved_at: str
    updated_at: str
    note: str = ""
    task_key: str = ""
    grade: str = DEFAULT_GRADE


@dataclass(frozen=True, slots=True)
class SourceRow:
    proposal_id: str
    project: str
    summary: str
    source_kind: str
    source_id: str
    source_subject: str
    source_sender: str
    source_date: str
    attachments: tuple[str, ...]
    approved_at: str
    grade: str = DEFAULT_GRADE


@dataclass(frozen=True, slots=True)
class ProjectView:
    name: str
    owner: str
    total: int
    done: int
    overdue: int
    next_due: str | None
    updated_at: str
    archived_at: str = ""
    archived_by: str = ""


@dataclass(frozen=True, slots=True)
class EventRow:
    at: str
    todo_id: str
    project: str
    kind: str
    detail: str
    actor: str = ""


@dataclass(frozen=True, slots=True)
class PendingView:
    """A proposal still waiting for the owner — shown as waiting, never as data."""

    request_id: str
    project: str
    todo_count: int
    source_subject: str
    created_at: str
    thread_url: str = ""
    grade: str = DEFAULT_GRADE


@dataclass(frozen=True, slots=True)
class BoardSnapshot:
    today: str
    generated_at: str
    projects: tuple[ProjectView, ...] = ()
    archived: tuple[ProjectView, ...] = ()
    todos: tuple[TodoRow, ...] = ()
    sources: tuple[SourceRow, ...] = ()
    events: tuple[EventRow, ...] = ()
    pending: tuple[PendingView, ...] = field(default=())
    withheld: int = 0

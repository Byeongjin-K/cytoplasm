"""The approved project data: only an owner-approved proposal ever writes here.

``apply_proposal`` is idempotent per proposal id — a watcher that dies after the insert
and runs again adds nothing. Progress updates (the "업무 실행 → 진행·완료 기록 갱신" loop)
change a todo's status/progress and append an event; they never add todos, so the set of
work items stays exactly what the owner approved.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

from .models import (
    DEFAULT_GRADE,
    NEVER_EXPORTED,
    OPEN_STATUSES,
    STATUSES,
    BoardSnapshot,
    EventRow,
    PendingView,
    ProjectBoardError,
    ProjectView,
    Proposal,
    SourceRow,
    TodoRow,
    grade_rank,
)

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS projects (
  name       TEXT PRIMARY KEY,
  owner      TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  proposal_id    TEXT PRIMARY KEY,
  project        TEXT NOT NULL,
  summary        TEXT NOT NULL,
  source_kind    TEXT NOT NULL,
  source_id      TEXT NOT NULL,
  source_subject TEXT NOT NULL,
  source_sender  TEXT NOT NULL,
  source_date    TEXT NOT NULL,
  attachments    TEXT NOT NULL,
  approved_at    TEXT NOT NULL,
  message_id     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS todos (
  todo_id        TEXT PRIMARY KEY,
  proposal_id    TEXT NOT NULL,
  project        TEXT NOT NULL,
  title          TEXT NOT NULL,
  owner          TEXT NOT NULL,
  due            TEXT,
  status         TEXT NOT NULL,
  progress       INTEGER NOT NULL,
  source_kind    TEXT NOT NULL,
  source_id      TEXT NOT NULL,
  source_subject TEXT NOT NULL,
  approved_at    TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  note           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_todos_project ON todos (project);
CREATE TABLE IF NOT EXISTS tasks (
  task_key   TEXT PRIMARY KEY,
  project    TEXT NOT NULL,
  grade      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  at      TEXT NOT NULL,
  todo_id TEXT NOT NULL,
  project TEXT NOT NULL,
  kind    TEXT NOT NULL,
  detail  TEXT NOT NULL
);
"""


def task_key_of(kind: str, source_id: str, subject: str) -> str:
    """The 업무 a source belongs to: its mail thread (subject without Re/Fw) or its minutes id."""
    from .proposals import conversation_of

    return f"{kind}:{conversation_of(kind, source_id, subject)}"


def _migrate(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(todos)")}
    if "task_key" not in columns:
        connection.execute("ALTER TABLE todos ADD COLUMN task_key TEXT NOT NULL DEFAULT ''")
        rows = connection.execute("SELECT todo_id, source_kind, source_id, source_subject FROM todos").fetchall()
        for todo_id, kind, source_id, subject in rows:
            connection.execute(
                "UPDATE todos SET task_key = ? WHERE todo_id = ?", (task_key_of(kind, source_id, subject), todo_id)
            )


def utc_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class BoardStore:
    """SQLite file under the private state root (directory 0700, file 0600)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fresh = not self.path.exists()
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            if fresh:
                self.path.chmod(0o600)
            connection.row_factory = sqlite3.Row
            connection.executescript(_SCHEMA)
            _migrate(connection)
            yield connection
            connection.commit()
        finally:
            connection.close()

    def apply_proposal(
        self, proposal_id: str, proposal: Proposal, *, message_id: str, now: datetime | None = None
    ) -> int:
        """Insert the approved proposal; returns the number of todos added (0 when already applied)."""
        stamp = utc_stamp(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sources WHERE proposal_id = ?", (proposal_id,)).fetchone():
                return 0
            current = db.execute(
                "SELECT owner FROM projects WHERE name = ?", (proposal.project,)
            ).fetchone()
            if current is None:
                db.execute(
                    "INSERT INTO projects VALUES (?, ?, ?, ?)",
                    (proposal.project, proposal.project_owner, stamp, stamp),
                )
            else:
                owner = proposal.project_owner or str(current["owner"])
                db.execute(
                    "UPDATE projects SET owner = ?, updated_at = ? WHERE name = ?",
                    (owner, stamp, proposal.project),
                )
            source = proposal.source
            task_key = task_key_of(source.kind, source.source_id, source.subject)
            self._set_task_grade(db, task_key, proposal.project, proposal.grade, stamp)
            db.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal_id, proposal.project, proposal.summary, source.kind, source.source_id,
                    source.subject, source.sender, source.date,
                    json.dumps(list(source.attachments), ensure_ascii=False), stamp, message_id,
                ),
            )
            for index, todo in enumerate(proposal.todos, start=1):
                todo_id = f"{proposal_id}-{index}"
                db.execute(
                    "INSERT INTO todos (todo_id, proposal_id, project, title, owner, due, status, progress,"
                    " source_kind, source_id, source_subject, approved_at, updated_at, note, task_key)"
                    " VALUES (?, ?, ?, ?, ?, ?, '열림', 0, ?, ?, ?, ?, ?, '', ?)",
                    (
                        todo_id, proposal_id, proposal.project, todo.title, todo.owner, todo.due,
                        source.kind, source.source_id, source.subject, stamp, stamp, task_key,
                    ),
                )
                db.execute(
                    "INSERT INTO events (at, todo_id, project, kind, detail) VALUES (?, ?, ?, ?, ?)",
                    (stamp, todo_id, proposal.project, "approved", todo.title),
                )
            return len(proposal.todos)

    @staticmethod
    def _set_task_grade(db: sqlite3.Connection, task_key: str, project: str, grade: str, stamp: str) -> None:
        """An approved card's grade becomes the 업무's grade — every todo of the 업무 follows it."""
        current = db.execute("SELECT grade FROM tasks WHERE task_key = ?", (task_key,)).fetchone()
        chosen = grade or (str(current["grade"]) if current else DEFAULT_GRADE)
        if current is None:
            db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?)", (task_key, project, chosen, stamp))
        elif str(current["grade"]) != chosen:
            db.execute("UPDATE tasks SET grade = ?, updated_at = ? WHERE task_key = ?", (chosen, stamp, task_key))
            db.execute(
                "INSERT INTO events (at, todo_id, project, kind, detail) VALUES (?, ?, ?, ?, ?)",
                (stamp, "", project, "grade", f"{current['grade']}→{chosen}"),
            )

    def task_grade(self, task_key: str) -> str | None:
        """The owner-approved grade of an 업무, or ``None`` before its first approved card."""
        with self._connect() as db:
            row = db.execute("SELECT grade FROM tasks WHERE task_key = ?", (task_key,)).fetchone()
        return None if row is None else str(row["grade"])

    def raise_grade(self, todo_id: str, grade: str, *, now: datetime | None = None) -> str:
        """Make the 업무 of ``todo_id`` stricter. Lowering a grade only happens through an approved card."""
        rank = grade_rank(grade)
        stamp = utc_stamp(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT task_key, project FROM todos WHERE todo_id = ?", (todo_id,)).fetchone()
            if row is None:
                raise ProjectBoardError(f"no approved todo with id {todo_id}")
            current = db.execute("SELECT grade FROM tasks WHERE task_key = ?", (row["task_key"],)).fetchone()
            before = str(current["grade"]) if current else DEFAULT_GRADE
            if rank < grade_rank(before):
                raise ProjectBoardError(
                    f"grade can only be raised here ({before} → {grade} lowers it); lower it through a revised card"
                )
            self._set_task_grade(db, str(row["task_key"]), str(row["project"]), grade, stamp)
        return str(row["task_key"])

    def record_progress(
        self,
        todo_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        note: str | None = None,
        now: datetime | None = None,
    ) -> TodoRow:
        if status is not None and status not in STATUSES:
            raise ProjectBoardError(f"status must be one of {', '.join(STATUSES)}")
        if progress is not None and not 0 <= progress <= 100:
            raise ProjectBoardError("progress must be between 0 and 100")
        if status is None and progress is None and note is None:
            raise ProjectBoardError("nothing to record: give a status, a progress or a note")
        stamp = utc_stamp(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM todos WHERE todo_id = ?", (todo_id,)).fetchone()
            if row is None:
                raise ProjectBoardError(f"no approved todo with id {todo_id}")
            new_status = status or str(row["status"])
            new_progress = progress if progress is not None else int(row["progress"])
            if status == "완료" and progress is None:
                new_progress = 100
            if status == "열림" and progress is None and row["status"] == "완료":
                new_progress = 0
            new_note = str(row["note"]) if note is None else " ".join(note.split())[:300]
            db.execute(
                "UPDATE todos SET status = ?, progress = ?, note = ?, updated_at = ? WHERE todo_id = ?",
                (new_status, new_progress, new_note, stamp, todo_id),
            )
            detail = f"{row['status']}→{new_status} {row['progress']}→{new_progress}%"
            if note:
                detail += f" · {new_note}"
            db.execute(
                "INSERT INTO events (at, todo_id, project, kind, detail) VALUES (?, ?, ?, ?, ?)",
                (stamp, todo_id, str(row["project"]), "progress", detail),
            )
            db.execute(
                "UPDATE projects SET updated_at = ? WHERE name = ?", (stamp, str(row["project"]))
            )
            updated = db.execute("SELECT * FROM todos WHERE todo_id = ?", (todo_id,)).fetchone()
        return _todo(updated)

    def todo(self, todo_id: str) -> TodoRow | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM todos WHERE todo_id = ?", (todo_id,)).fetchone()
        return None if row is None else _todo(row)

    def open_todo_lines(self, limit: int = 40) -> tuple[str, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT project, title, due FROM todos WHERE status != '완료' ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(f"{row['project']} · {row['title']}" + (f" ({row['due']})" if row["due"] else "") for row in rows)

    def project_names(self) -> tuple[str, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT name FROM projects ORDER BY name").fetchall()
        return tuple(str(row["name"]) for row in rows)

    def snapshot(
        self, *, today: date, pending: tuple[PendingView, ...] = (), now: datetime | None = None,
        exported: bool = False,
    ) -> BoardSnapshot:
        """The board view. ``exported=True`` is the copy that leaves the owner's screen: no 대외비·기관지정."""
        with self._connect() as db:
            todos = tuple(
                _todo(row)
                for row in db.execute(
                    "SELECT todos.*, COALESCE(tasks.grade, ?) AS grade FROM todos"
                    " LEFT JOIN tasks ON tasks.task_key = todos.task_key"
                    " ORDER BY due IS NULL, due, todos.project, todo_id",
                    (DEFAULT_GRADE,),
                ).fetchall()
            )
            source_grades = {
                str(r["proposal_id"]): str(r["grade"])
                for r in db.execute(
                    "SELECT DISTINCT todos.proposal_id, COALESCE(tasks.grade, ?) AS grade FROM todos"
                    " LEFT JOIN tasks ON tasks.task_key = todos.task_key",
                    (DEFAULT_GRADE,),
                ).fetchall()
            }
            sources = tuple(
                SourceRow(
                    proposal_id=str(row["proposal_id"]), project=str(row["project"]),
                    summary=str(row["summary"]), source_kind=str(row["source_kind"]),
                    source_id=str(row["source_id"]), source_subject=str(row["source_subject"]),
                    source_sender=str(row["source_sender"]), source_date=str(row["source_date"]),
                    attachments=tuple(json.loads(str(row["attachments"]))),
                    approved_at=str(row["approved_at"]),
                    grade=source_grades.get(str(row["proposal_id"]), DEFAULT_GRADE),
                )
                for row in db.execute("SELECT * FROM sources ORDER BY approved_at DESC").fetchall()
            )
            events = tuple(
                EventRow(str(r["at"]), str(r["todo_id"]), str(r["project"]), str(r["kind"]), str(r["detail"]))
                for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30").fetchall()
            )
            projects = db.execute("SELECT * FROM projects ORDER BY name").fetchall()
        withheld = 0
        if exported:
            kept = tuple(todo for todo in todos if todo.grade not in NEVER_EXPORTED)
            withheld = len(todos) - len(kept)
            kept_ids = {todo.todo_id for todo in kept}
            todos = kept
            sources = tuple(source for source in sources if source.grade not in NEVER_EXPORTED)
            events = tuple(event for event in events if event.todo_id in kept_ids)
            pending = tuple(view for view in pending if view.grade not in NEVER_EXPORTED)
            visible = {todo.project for todo in todos} | {source.project for source in sources}
            projects = [project for project in projects if project["name"] in visible]
        iso_today = today.isoformat()
        views: list[ProjectView] = []
        for project in projects:
            mine = [todo for todo in todos if todo.project == project["name"]]
            open_dues = sorted(t.due for t in mine if t.due and t.status in OPEN_STATUSES)
            views.append(
                ProjectView(
                    name=str(project["name"]), owner=str(project["owner"]), total=len(mine),
                    done=sum(1 for t in mine if t.status == "완료"),
                    overdue=sum(1 for d in open_dues if d < iso_today),
                    next_due=next((d for d in open_dues if d >= iso_today), None),
                    updated_at=str(project["updated_at"]),
                )
            )
        return BoardSnapshot(
            today=iso_today, generated_at=utc_stamp(now), projects=tuple(views), todos=todos,
            sources=sources, events=events, pending=pending, withheld=withheld,
        )


def _todo(row: sqlite3.Row) -> TodoRow:
    return TodoRow(
        todo_id=str(row["todo_id"]), proposal_id=str(row["proposal_id"]), project=str(row["project"]),
        title=str(row["title"]), owner=str(row["owner"]),
        due=None if row["due"] is None else str(row["due"]), status=str(row["status"]),
        progress=int(row["progress"]), source_kind=str(row["source_kind"]),
        source_id=str(row["source_id"]), source_subject=str(row["source_subject"]),
        approved_at=str(row["approved_at"]), updated_at=str(row["updated_at"]), note=str(row["note"]),
        task_key=str(row["task_key"]),
        grade=str(row["grade"]) if "grade" in row.keys() else DEFAULT_GRADE,
    )

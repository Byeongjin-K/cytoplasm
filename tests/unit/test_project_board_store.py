"""Approved project data and the strict model-answer parser."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from automation.project_board.analysis import parse_answer
from automation.project_board.board_store import BoardStore
from automation.project_board.models import ProjectBoardError, SourceRef, proposal_from_dict

SOURCE = SourceRef("mail", "u-1", "자료 요청", "a@example.invalid", "2026-10-13T09:00:00")


def _proposal(project: str = "프로젝트A", due: str | None = "2026-10-10") -> object:
    return proposal_from_dict({
        "project": project, "project_owner": "담당자1", "summary": "요약",
        "todos": [{"title": "보고서 제출", "owner": "담당자2", "due": due}, {"title": "회의 준비", "due": "2026-10-30"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "자료 요청", "attachments": ["a.pdf"]},
    })


def test_apply_is_idempotent_per_proposal(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    assert store.apply_proposal("pb-1", _proposal(), message_id="m1") == 2  # type: ignore[arg-type]
    assert store.apply_proposal("pb-1", _proposal(), message_id="m1") == 0  # type: ignore[arg-type]
    snapshot = store.snapshot(today=date(2026, 10, 13))
    assert len(snapshot.todos) == 2 and snapshot.sources[0].attachments == ("a.pdf",)
    assert (tmp_path / "board.db").stat().st_mode & 0o777 == 0o600


def test_project_view_counts_overdue_and_next_due(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", _proposal(), message_id="m1")  # type: ignore[arg-type]
    view = store.snapshot(today=date(2026, 10, 13)).projects[0]
    assert (view.name, view.owner, view.total, view.done, view.overdue, view.next_due) == (
        "프로젝트A", "담당자1", 2, 0, 1, "2026-10-30")


def test_progress_updates_status_and_appends_an_event(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", _proposal(), message_id="m1")  # type: ignore[arg-type]
    todo = store.record_progress("pb-1-1", status="완료", note="제출함")
    assert (todo.status, todo.progress, todo.note) == ("완료", 100, "제출함")
    reopened = store.record_progress("pb-1-1", status="열림")
    assert (reopened.status, reopened.progress) == ("열림", 0)
    snapshot = store.snapshot(today=date(2026, 10, 13))
    assert [e.kind for e in snapshot.events][:2] == ["progress", "progress"]
    assert len(snapshot.todos) == 2


@pytest.mark.parametrize(
    "kwargs", [{"status": "끝"}, {"progress": 101}, {}],
)
def test_progress_rejects_bad_input(tmp_path: Path, kwargs: dict[str, object]) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", _proposal(), message_id="m1")  # type: ignore[arg-type]
    with pytest.raises(ProjectBoardError):
        store.record_progress("pb-1-1", **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ProjectBoardError):
        store.record_progress("pb-9-9", status="완료")


def test_parser_accepts_a_fenced_answer_and_keeps_the_source() -> None:
    answer = "```json\n" + json.dumps({
        "relevant": True, "project": "프로젝트A", "summary": "s",
        "todos": [{"title": "제출", "owner": "", "due": None, "due_reason": "원문에 기한 없음"}],
    }, ensure_ascii=False) + "\n```"
    proposal = parse_answer(answer, SOURCE, provider="p", model="m")
    assert proposal is not None and proposal.source == SOURCE and proposal.todos[0].due is None


def test_unrelated_mail_makes_no_proposal() -> None:
    assert parse_answer('{"relevant": false}', SOURCE, provider="p", model="m") is None


@pytest.mark.parametrize(
    "answer",
    [
        "요약: 보고서를 내야 합니다",
        '{"project": "A", "summary": "s", "todos": []}',
        '{"relevant": true, "project": "A", "summary": "s", "todos": [{"title": "t", "due": "10월 20일"}]}',
        '{"relevant": true, "project": "", "summary": "s", "todos": []}',
    ],
)
def test_malformed_answers_are_retryable_errors_not_guesses(answer: str) -> None:
    with pytest.raises(ProjectBoardError) as error:
        parse_answer(answer, SOURCE, provider="p", model="m")
    assert error.value.retryable

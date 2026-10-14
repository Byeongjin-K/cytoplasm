"""업무 단위 보안 등급: one grade per 업무 (mail thread / minutes), inherited by every todo, never exported."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from automation.project_board.analysis import parse_answer
from automation.project_board.board_store import BoardStore
from automation.project_board.config import load_config
from automation.project_board.dashboard import render_dashboard
from automation.project_board.models import (
    PendingView,
    ProjectBoardError,
    SourceRef,
    proposal_from_dict,
)
from automation.project_board.proposal_render import render_proposal_card
from automation.project_board.proposals import new_record

TODAY = date(2026, 10, 14)


def _proposal(
    source_id: str, subject: str, *, grade: str = "", project: str = "프로젝트A", titles: tuple[str, ...] = ("보고서 제출",)
) -> object:
    raw: dict[str, object] = {
        "project": project, "summary": "요약",
        "todos": [{"title": title, "due": "2026-10-30"} for title in titles],
        "source": {"kind": "mail", "source_id": source_id, "subject": subject},
    }
    if grade:
        raw["grade"] = grade
    return proposal_from_dict(raw)


def _config(tmp_path: Path, projects: list[dict[str, object]]) -> dict[str, str]:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"projects": projects}, ensure_ascii=False), encoding="utf-8")
    return {"PROJECT_BOARD_CONFIG": str(path), "HOME": str(tmp_path)}


def test_an_ungraded_proposal_serializes_as_before_so_pending_approvals_keep_their_hash() -> None:
    assert "grade" not in _proposal("u-1", "자료 요청").to_dict()  # type: ignore[attr-defined]
    assert _proposal("u-1", "자료 요청", grade="대외비").to_dict()["grade"] == "대외비"  # type: ignore[attr-defined]
    with pytest.raises(ProjectBoardError):
        _proposal("u-1", "자료 요청", grade="극비")


def test_an_approved_grade_covers_every_todo_of_the_thread_and_moves_them_together(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", _proposal("u-1", "핵심기기 설계 검토", grade="과제내부"), message_id="m1")  # type: ignore[arg-type]
    store.apply_proposal("pb-2", _proposal("u-2", "Re: 핵심기기 설계 검토", titles=("회의 준비",)), message_id="m2")  # type: ignore[arg-type]
    store.apply_proposal("pb-3", _proposal("u-3", "다른 업무"), message_id="m3")  # type: ignore[arg-type]
    grades = {t.todo_id: t.grade for t in store.snapshot(today=TODAY).todos}
    assert grades == {"pb-1-1": "과제내부", "pb-2-1": "과제내부", "pb-3-1": "과제내부"}

    store.apply_proposal("pb-4", _proposal("u-4", "RE: 핵심기기 설계 검토", grade="대외비", titles=("도면 검토",)), message_id="m4")  # type: ignore[arg-type]
    snapshot = store.snapshot(today=TODAY)
    grades = {t.todo_id: t.grade for t in snapshot.todos}
    assert grades == {"pb-1-1": "대외비", "pb-2-1": "대외비", "pb-4-1": "대외비", "pb-3-1": "과제내부"}
    assert {s.proposal_id: s.grade for s in snapshot.sources}["pb-1"] == "대외비"
    assert [e.detail for e in snapshot.events if e.kind == "grade"] == ["과제내부→대외비"]


def test_the_cli_path_only_tightens_a_grade(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", _proposal("u-1", "설계 검토", grade="대외비"), message_id="m1")  # type: ignore[arg-type]
    with pytest.raises(ProjectBoardError):
        store.raise_grade("pb-1-1", "공개")
    store.raise_grade("pb-1-1", "기관지정")
    assert store.snapshot(today=TODAY).todos[0].grade == "기관지정"
    with pytest.raises(ProjectBoardError):
        store.raise_grade("pb-9-9", "기관지정")


def test_suggested_grade_never_drops_below_the_project_default_and_approved_tasks_win(tmp_path: Path) -> None:
    from automation.project_board.service import Board, task_grade_for

    config = load_config(_config(tmp_path, [{"name": "프로젝트A", "grade": "대외비"}, {"name": "프로젝트B"}]))
    board = Board(tmp_path / "board")
    assert task_grade_for(board, config, _proposal("u-1", "새 업무", grade="공개")) == "대외비"  # type: ignore[arg-type]
    assert task_grade_for(board, config, _proposal("u-1", "새 업무", project="프로젝트B")) == "과제내부"  # type: ignore[arg-type]
    assert task_grade_for(board, config, _proposal("u-1", "새 업무", project="프로젝트B", grade="기관지정")) == "기관지정"  # type: ignore[arg-type]

    board.store.apply_proposal("pb-1", _proposal("u-1", "공모 안내", project="프로젝트B", grade="공개"), message_id="m")  # type: ignore[arg-type]
    later = _proposal("u-2", "Re: 공모 안내", project="프로젝트B", grade="대외비")
    assert task_grade_for(board, config, later) == "공개"  # type: ignore[arg-type]


def test_config_rejects_an_unknown_project_grade(tmp_path: Path) -> None:
    with pytest.raises(ProjectBoardError):
        load_config(_config(tmp_path, [{"name": "프로젝트A", "grade": "비밀"}]))


def test_the_model_may_suggest_a_grade_but_an_unknown_one_is_dropped() -> None:
    source = SourceRef("mail", "u-1", "s", "a@example.invalid", "2026-10-13T09:00:00")
    answer = {"relevant": True, "project": "프로젝트A", "summary": "s", "todos": [{"title": "t", "due": None}]}
    suggested = parse_answer(json.dumps({**answer, "grade": "대외비"}, ensure_ascii=False), source, provider="p", model="m")
    unknown = parse_answer(json.dumps({**answer, "grade": "극비"}, ensure_ascii=False), source, provider="p", model="m")
    assert suggested is not None and suggested.grade == "대외비"
    assert unknown is not None and unknown.grade == ""


def test_the_exported_copy_never_carries_a_secure_task(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", _proposal("u-1", "보안연구 주제 논의", project="보안과제", grade="대외비", titles=("비밀실험 계획",)), message_id="m1")  # type: ignore[arg-type]
    store.apply_proposal("pb-2", _proposal("u-2", "지정자료 회신", project="기관과제", grade="기관지정", titles=("지정자료 제출",)), message_id="m2")  # type: ignore[arg-type]
    store.apply_proposal("pb-3", _proposal("u-3", "공모 안내", grade="공개", titles=("공모 응모",)), message_id="m3")  # type: ignore[arg-type]
    store.record_progress("pb-1-1", status="진행", note="비밀메모")
    pending = (
        PendingView("pb-4", "보안과제", 1, "대기중 보안건", "2026-10-14T00:00:00Z", grade="대외비"),
        PendingView("pb-5", "프로젝트A", 1, "대기중 일반건", "2026-10-14T00:00:00Z", grade="과제내부"),
    )

    owner_view = store.snapshot(today=TODAY, pending=pending)
    assert len(owner_view.todos) == 3 and owner_view.withheld == 0

    exported = store.snapshot(today=TODAY, pending=pending, exported=True)
    assert [t.todo_id for t in exported.todos] == ["pb-3-1"]
    assert [s.proposal_id for s in exported.sources] == ["pb-3"]
    assert [p.request_id for p in exported.pending] == ["pb-5"]
    assert {v.name for v in exported.projects} == {"프로젝트A"}
    assert all(e.todo_id == "pb-3-1" for e in exported.events) and exported.withheld == 2

    page = render_dashboard(exported, mode="file")
    for secret in ("보안연구", "비밀실험", "비밀메모", "지정자료", "보안과제", "기관과제", "대기중 보안건"):
        assert secret not in page
    assert "공모 응모" in page and "2건은 이 사본에서 뺐습니다" in page


def test_the_digest_attachment_is_the_exported_copy(tmp_path: Path) -> None:
    from automation.project_board.service import Board, digest_snapshot

    board = Board(tmp_path / "board")
    board.store.apply_proposal("pb-1", _proposal("u-1", "보안연구 주제", grade="대외비", titles=("비밀실험",)), message_id="m")  # type: ignore[arg-type]
    page = digest_snapshot(board, today=TODAY).read_text(encoding="utf-8")
    assert "비밀실험" not in page and "보안연구" not in page


def test_the_owner_dashboard_shows_the_grade_badge(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", _proposal("u-1", "보안연구 주제", grade="대외비"), message_id="m")  # type: ignore[arg-type]
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token="t")
    assert "대외비 · 외부 비공개" in page and "보고서 제출" in page


def test_a_board_from_before_grades_migrates_and_reads_the_default(tmp_path: Path) -> None:
    path = tmp_path / "board.db"
    BoardStore(path).apply_proposal("pb-1", _proposal("u-1", "Re: 옛 업무"), message_id="m")  # type: ignore[arg-type]
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE tasks")
        db.execute("ALTER TABLE todos DROP COLUMN task_key")
        db.execute("PRAGMA user_version = 0")
    store = BoardStore(path)
    todo = store.snapshot(today=TODAY).todos[0]
    assert (todo.task_key, todo.grade) == ("mail:옛 업무", "과제내부")
    store.apply_proposal("pb-2", _proposal("u-2", "옛 업무", grade="대외비", titles=("후속",)), message_id="m2")  # type: ignore[arg-type]
    assert {t.grade for t in store.snapshot(today=TODAY).todos} == {"대외비"}


def test_the_card_states_the_grade_and_its_reach_and_v1_cards_stay_as_posted() -> None:
    proposal = _proposal("u-1", "설계 검토", grade="대외비")
    record = new_record(proposal, created_at="2026-10-14T00:00:00Z")  # type: ignore[arg-type]
    card = render_proposal_card(record, proposal)  # type: ignore[arg-type]
    assert "보안 등급: 대외비" in card and "이 업무의 할 일·이후 카드 전부" in card and "밖으로 내보내지 않음" in card
    old = render_proposal_card(record, proposal, render_version="project-proposal-v1")  # type: ignore[arg-type]
    assert "보안 등급" not in old and "project-proposal-v1" in old


def test_revise_can_change_the_grade_of_a_pending_card() -> None:
    from automation.project_board.service import Revision, revised_proposal

    changed = revised_proposal(_proposal("u-1", "설계 검토"), Revision(grade="기관지정"))  # type: ignore[arg-type]
    assert changed.grade == "기관지정"
    with pytest.raises(ProjectBoardError):
        revised_proposal(_proposal("u-1", "설계 검토"), Revision(grade="극비"))  # type: ignore[arg-type]

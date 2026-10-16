"""A demo board: invented projects, people and mail, served on loopback.

``python3 -m automation.project_board demo`` lets the dashboard be shown to someone else
without any real 과제, person or mail on screen. Every name below is invented; the board
lives in a fresh directory (never the live board) and is thrown away when the server stops.
Dates are relative to today, so the demo always has overdue, this-week and later work.
"""

from __future__ import annotations

import secrets
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

from .board_store import BoardStore, _live_board
from .models import PendingView, ProjectBoardError, proposal_from_dict

_PROJECTS: Final = (
    ("고체전해질 소재 개발", "윤서진", "과제내부", (
        ("2차년도 중간보고서 초안 제출", "윤서진", -2),
        ("시편 12종 이온전도도 측정", "한도윤", 3),
        ("공동연구기관 시료 인수 확인", "윤서진", 9),
        ("특허 출원 명세서 검토", "", 24),
    ), "[소재과제] 2차년도 중간점검 일정 안내", "연구관리팀", ("중간점검_양식.hwpx", "평가지표.xlsx")),
    ("수소 혼소 터빈 실증", "강민재", "대외비", (
        ("연소기 시험 결과 정리", "강민재", 1),
        ("실증 부지 안전점검 일정 확정", "오하린", 5),
        ("협력사 납품 검수", "강민재", None),
    ), "실증 설비 안전점검 협조 요청", "협력사 품질팀", ("점검표.pdf",)),
    ("스마트공장 데이터 표준화", "정유나", "공개", (
        ("표준안 의견수렴 회의록 배포", "정유나", 0),
        ("데이터 사전 v0.9 검토", "임지호", 12),
    ), "표준화 워킹그룹 3차 회의 결과", "표준화 사무국", ()),
    ("해양 관측 센서 플랫폼", "배준서", "과제내부", (
        ("부이 통신모듈 펌웨어 갱신", "배준서", -5),
        ("관측 데이터 품질 보고", "배준서", 16),
    ), "현장 관측 데이터 이관 일정", "해양관측센터", ("이관계획.pdf",)),
)
_ARCHIVED: Final = ("열교환기 성능 평가", "최가은", (("최종보고서 제출", "최가은", -40),),
                    "최종평가 결과 통보", "연구관리팀")
_DONE: Final = {("고체전해질 소재 개발", 0), ("스마트공장 데이터 표준화", 0), ("해양 관측 센서 플랫폼", 1)}
_DOING: Final = {("수소 혼소 터빈 실증", 0): 60, ("고체전해질 소재 개발", 1): 40}


def _proposal(project: str, owner: str, grade: str, todos: tuple, subject: str, sender: str,
              attachments: tuple[str, ...], index: int, today: date) -> dict[str, object]:
    return {
        "project": project, "project_owner": owner, "grade": grade,
        "summary": f"{subject}에 따라 {len(todos)}건의 할 일을 정리했습니다.",
        "todos": [{"title": t, "owner": o, "due": None if d is None else (today + timedelta(days=d)).isoformat()}
                  for t, o, d in todos],
        "source": {"kind": "mail", "source_id": f"demo-{index}", "subject": subject, "sender": sender,
                   "date": (today - timedelta(days=3 + index)).isoformat(), "attachments": list(attachments)},
    }


def seed(store: BoardStore, today: date) -> None:
    base = datetime.combine(today - timedelta(days=4), datetime.min.time(), UTC)
    for index, (project, owner, grade, todos, subject, sender, files) in enumerate(_PROJECTS, 1):
        raw = _proposal(project, owner, grade, todos, subject, sender, files, index, today)
        store.apply_proposal(f"demo-{index}", proposal_from_dict(raw), message_id=f"demo-{index}",
                             now=base + timedelta(hours=index))
        for position, _ in enumerate(todos):
            todo_id = f"demo-{index}-{position + 1}"
            if (project, position) in _DONE:
                store.record_progress(todo_id, status="완료", now=base + timedelta(days=2, hours=index), by="데모 사용자")
            elif (project, position) in _DOING:
                store.record_progress(todo_id, status="진행", progress=_DOING[(project, position)],
                                      now=base + timedelta(days=3, hours=index), by="데모 사용자")
    project, owner, todos, subject, sender = _ARCHIVED
    raw = _proposal(project, owner, "과제내부", todos, subject, sender, (), 9, today)
    store.apply_proposal("demo-9", proposal_from_dict(raw), message_id="demo-9", now=base)
    store.record_progress("demo-9-1", status="완료", now=base + timedelta(hours=1), by="데모 사용자")
    store.set_archived(project, True, by="데모 사용자", now=base + timedelta(days=1))


def pending(today: date) -> tuple[PendingView, ...]:
    return (PendingView(request_id="demo-pending", project="스마트공장 데이터 표준화", todo_count=2,
                        source_subject="표준안 공청회 개최 안내", created_at=today.isoformat(), grade="공개"),)


def mail_text(source_id: str) -> str | None:
    for index, (project, _, _, _, subject, sender, _) in enumerate(_PROJECTS, 1):
        if source_id == f"demo-{index}":
            return f"# {subject}\n\n보낸 사람: {sender}\n\n{project} 관련 데모 메일입니다. 실제 내용이 아닙니다.\n"
    return None


def make_demo_board(root: Path | None, today: date) -> BoardStore:
    """A seeded store in ``root`` (a new temporary directory by default); the live board is refused."""
    directory = root or Path(tempfile.mkdtemp(prefix="project-board-demo-"))
    path = directory / "board.db"
    if _live_board(path) or directory.resolve().is_relative_to((Path.home() / ".hermes" / "project-board").resolve()):
        raise ProjectBoardError("the demo never writes the live board; choose another --root")
    if path.exists():
        raise ProjectBoardError(f"{path} already exists; the demo needs an empty directory")
    if directory.exists() and any(directory.iterdir()):
        raise ProjectBoardError(f"{directory} is not an empty directory")
    directory.mkdir(parents=True, exist_ok=True)
    store = BoardStore(path)
    seed(store, today)
    return store


def demo_token() -> str:
    return secrets.token_urlsafe(32)


def demo_handlers(today: Callable[[], date]) -> dict[str, Callable[..., object]]:
    return {
        "pending": lambda: pending(today()),
        "read_source": lambda kind, source_id: mail_text(source_id) if kind == "mail" else None,
        "attachment_path": lambda kind, source_id, name: None,
        "on_change": lambda: None,
    }

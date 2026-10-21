"""A demo board: invented projects, people and mail, served on loopback.

``python3 -m automation.project_board demo`` lets the dashboard be shown to someone else
without any real 과제, person or mail on screen. Every name below is invented; the board
lives in a fresh directory (never the live board) and is thrown away when the server stops.
Dates are relative to today, so the demo always has overdue, this-week and later work.
"""

from __future__ import annotations

import random
import secrets
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

from .board_store import BoardStore, _live_board
from .models import PendingView, ProjectBoardError, TodoExtra, proposal_from_dict
from .sessions import Sessions, parse_sessions

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


_THEMES: Final = (
    ("ops", "지침 정리", 9), ("ops", "알림 표시", 6), ("ops", "세션 복구", 4), ("assistant", "메일 정리", 7),
    ("assistant", "일정 안내", 3), ("research", "실험 기록", 5), ("research", "자료 검토", 3),
    ("web", "대시보드 화면", 6), ("study", "동향 조사", 4), ("infra", "장비 점검", 3),
)
_SERVICES: Final = (
    ("메신저 수신", "watch", "상시", 2), ("레인 감시", "watch", "상시", 1), ("일정 감시", "watch", "상시", 1),
    ("웹 대시보드", "web", "상시", 1), ("팀 할 일 화면", "web", "상시", 2),
    ("아침 점검", "timer", "예약", 1), ("백업", "timer", "예약", 1), ("업데이트 확인", "timer", "자동", 1),
)


def sessions(now: datetime) -> Sessions:
    """An invented week of sessions ending a few minutes before ``now``; the same shape the collector writes."""
    rng = random.Random(16)
    end = int(now.timestamp() * 1000) - 4 * 60_000
    kst = timedelta(hours=9)
    w0 = int((datetime.combine((now + kst).date() - timedelta(days=7), datetime.min.time(), UTC) - kst).timestamp() * 1000)
    lanes = []
    for dept, theme, count in _THEMES:
        for i in range(count):
            start = rng.randint(w0 - 86_400_000, end - 3_600_000)
            open_ = i == 0 and dept in ("web", "ops")
            stop = None if open_ else min(end, max(w0, start + rng.randint(20, 600) * 60_000))
            st = "run" if open_ else ("review" if i == 1 and dept == "research" else "done")
            lanes.append({"dept": dept, "theme": theme, "st": st, "start": start, "end": stop,
                          "dur": (stop or end) - max(start, w0), "carried": start < w0})
    # what the collector writes for a lane parked on a person or a time, and for one whose agent stopped
    tomorrow_8 = int((datetime.combine((now + kst).date() + timedelta(days=1), datetime.min.time(), UTC) - kst).timestamp() * 1000) + 8 * 3_600_000
    for theme, start_h, since_h, extra in (
        ("메일 정리", 30, 3, {"why": "아침 다이제스트 화면 확인 대기", "until": tomorrow_8}),
        ("지침 정리", 20, 5, {"why": "소유자 승인(✅) 대기, v2.4.0 릴리스 카드"}),
        ("실험 기록", 9, 2, {"why": "소유자 결정 대기, 질문 Q12"}),
    ):
        dept = next(d for d, t, _ in _THEMES if t == theme)
        start = end - start_h * 3_600_000
        lanes.append({"dept": dept, "theme": theme, "st": "wait", "start": start, "end": None, "dur": end - max(start, w0),
                      "carried": False, "since": end - since_h * 3_600_000, **extra})
    start = end - 7 * 3_600_000
    lanes.append({"dept": "web", "theme": "대시보드 화면", "st": "block", "start": start, "end": None, "dur": end - start,
                  "carried": False, "why": "작업 창이 오류로 멈췄습니다"})
    agents = []
    for platform, families in (("claude", ("Opus 5.5", "Sonnet 5.5", "Haiku")), ("senpi", ("Opus 5.5", "기타 모델"))):
        for n in range(48 if platform == "claude" else 22):
            start = rng.randint(w0 - 43_200_000, end - 600_000)
            stop = min(end, max(w0, start + rng.randint(5, 240) * 60_000))
            agents.append({"platform": platform, "fam": families[n % len(families)], "start": max(start, w0), "end": stop,
                           "dur": stop - max(start, w0), "calls": rng.randint(3, 400), "live": stop > end - 15 * 60_000})
    aoe = [{"kind": kind, "start": end - hours * 3_600_000} for kind, hours in (("lead", 90), ("lane", 5), ("lane", 2), ("proj", 30))]
    services = [
        {"label": label, "group": group, "tag": tag, "units": units, "st": "live" if group != "timer" else "ok",
         "start": w0 - 86_400_000, "restarts": rng.randint(1, 4) if group != "timer" else None,
         "runs": 7 if group == "timer" else None, "fails": 0, "every": "매일" if group == "timer" else "",
         "last": end - 3 * 3_600_000 if group == "timer" else None, "lastOk": True if group == "timer" else None}
        for label, group, tag, units in _SERVICES
    ]
    # one timer failed twice earlier in the week and has run fine since; one is failing right now
    for row in services:
        if row["label"] == "백업":
            row.update({"fails": 2, "lastFail": end - 53 * 3_600_000})
        elif row["label"] == "업데이트 확인":
            last = end - 25 * 60_000
            row.update({"st": "fail", "every": "30분마다", "runs": 336, "fails": 3, "last": last, "lastOk": False, "lastFail": last})
    return parse_sessions({"w0": w0, "now": end, "lanes": lanes, "agents": agents, "aoe": aoe, "services": services})


def demo_token() -> str:
    return secrets.token_urlsafe(32)


def demo_handlers(today: Callable[[], date]) -> dict[str, Callable[..., object]]:
    return {
        "pending": lambda: pending(today()),
        "read_source": lambda kind, source_id: mail_text(source_id) if kind == "mail" else None,
        "attachment_path": lambda kind, source_id, name: None,
        "on_change": lambda: None,
        "sessions": lambda: sessions(_feed_slot(datetime.now(UTC))),
        "extras": lambda: _EXTRAS,
    }


_EXTRAS: Final = {  # invented, like everything else here: what an analysed mail says beyond the row
    "demo-1-2": TodoExtra("원문 기한", "14:00", "소재동 2층 측정실"),
    "demo-2-1": TodoExtra("원문 기한", "", ""),
    "demo-2-2": TodoExtra("협력사가 제안한 점검 주간", "10:30", "실증 부지 현장 사무소"),
    "demo-3-1": TodoExtra("회의 당일 배포", "16:00", "온라인"),
}


def _feed_slot(now: datetime) -> datetime:
    """``now`` floored to the 10 minutes the real feed takes, so an open demo page sees 304s between feeds."""
    return now.replace(minute=now.minute - now.minute % 10, second=0, microsecond=0)

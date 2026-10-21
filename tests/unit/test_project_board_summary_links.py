"""The summary earns its space (owner 2026-10-21 "이렇게 공간만 차지하는 기능을 모두 쓸모있게 해", Q346, Q347): every tile
opens its own bucket or filter, 승인 대기 gives way to the week's new todos when nothing waits, undated todos get a line,
the project cards stay on 프로젝트 and 보관함 is reached from its foot instead of the tab bar."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import TABS, render_dashboard
from automation.project_board.models import proposal_from_dict

TODAY = date(2026, 10, 14)


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "grade": "공개",
        "todos": [{"title": "늦은 일", "due": "2026-10-10"}, {"title": "이번 주 일", "due": "2026-10-16"}, {"title": "날짜 없는 일"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "요청"},
    }), message_id="pb-1")
    store.apply_proposal("pb-2", proposal_from_dict({
        "project": "프로젝트B", "summary": "요약", "todos": [{"title": "끝난 과제 일", "due": "2026-09-01"}],
        "source": {"kind": "mail", "source_id": "u-2", "subject": "옛 요청"},
    }), message_id="pb-2")
    store.set_archived("프로젝트B", True, by="시험")
    return store


def _summary(page: str) -> str:
    return page.split('<section id="summary"', 1)[1].split("</section>", 1)[0]


def test_tiles_open_their_bucket_or_filter_and_an_empty_wait_shows_new_todos(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token="t" * 40)
    tiles = re.findall(r'<a class="tile[^"]*" href="([^"]+)"><span class="tile-top">.*?</svg>([^<]+)</span>', _summary(page))
    assert [href for href, _ in tiles] == ["#sched-late", "#sched-week", "#todos?s=%EC%A7%84%ED%96%89", "#events"]
    assert tiles[3][1] == "최근 7일 새 할\u00a0일"
    assert 'id="sched-late"' in page and 'id="sched-week"' in page and 'id="sched-none"' in page
    assert 'href="#sched-none"' in _summary(page) and "마감이 정해지지 않은 할\u00a0일 1건" in _summary(page)


def test_the_summary_keeps_one_line_to_projects_and_the_shelf_moves_under_projects(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token="t" * 40)
    summary = _summary(page)
    assert "pcard" not in summary and '<a class="card ss-strip" href="#projects">' in summary
    assert "archive" not in [tab for tab, _ in TABS] and 'data-tab="archive"' not in page.split("<nav", 1)[1].split("</nav>", 1)[0]
    projects = page.split('<section id="projects"', 1)[1].split("</section>", 1)[0]
    assert 'class="card ss-strip shelf-link" href="#archive"' in projects
    assert '<section id="archive" data-tab="archive">' in page  # the old address still opens the shelf

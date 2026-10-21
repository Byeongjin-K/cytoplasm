"""Connectivity: every part of a 일정 row leads to its information (owner 2026-10-21: "일정 항목에서 개별 요소
클릭해도 해당 정보로 안넘어가져"). The title opens that todo on the 할 일 screen, the project its page, the subject
the source; nothing links to a todo anchor that is not on the page."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import project_href, render_dashboard
from automation.project_board.models import proposal_from_dict

TODAY = date(2026, 10, 14)


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    for pid, project, titles in (("pb-1", "프로젝트A", ("보고서 제출", "일정 취합")), ("pb-2", "프로젝트B", ("발표 준비",))):
        store.apply_proposal(pid, proposal_from_dict({
            "project": project, "summary": "요약", "grade": "공개",
            "todos": [{"title": title, "due": "2026-10-20"} for title in titles],
            "source": {"kind": "mail", "source_id": f"{pid}-u", "subject": f"{project} 요청"},
        }), message_id=pid)
    return store


def _schedule_rows(page: str) -> list[str]:
    panel = page.split('<section id="schedule"', 1)[1].split("</section>", 1)[0]
    return re.findall(r"<li data-project=.*?</li>", panel, re.S)


def test_a_schedule_row_links_its_title_project_and_source(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token="t" * 40)
    rows = _schedule_rows(page)
    assert len(rows) == 3
    row = next(r for r in rows if "보고서 제출" in r)
    assert '<a class="ag-title" href="#todo-pb-1-1">보고서 제출</a>' in row
    assert f'href="{project_href("프로젝트A")}"' in row
    assert 'href="/source/mail/pb-1-u"' in row
    assert 'id="todo-pb-1-1"' in page  # the title's target is on the page


def test_no_todo_link_points_at_a_missing_anchor_and_archived_rows_stay_plain(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_archived("프로젝트B", True, by="시험")
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token="t" * 40)
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    targets = re.findall(r'href="#(todo-[^"]+)"', page)
    assert targets and all(target in ids for target in targets)
    archived = page.split('data-project="프로젝트B" data-archived="1">', 1)[1].split("</section>", 1)[0]
    assert "<b>발표 준비</b>" in archived and 'class="ag-title"' not in archived


def test_the_mailed_copy_keeps_schedule_titles_linked_but_has_no_source_links(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="file")
    rows = _schedule_rows(page)
    assert all('class="ag-title" href="#todo-' in r for r in rows)
    assert all("/source/" not in r for r in rows)

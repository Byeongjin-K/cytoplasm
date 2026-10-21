"""Connectivity, one screen per thing (owner 2026-10-21: "일정 항목에서 개별 요소 클릭해도 해당 정보로 안넘어가져",
"이런 연결성이 ssot 개념으로 잘 되있으면 좋겠어"). Every todo has one screen at ``#todo/<id>`` and every owner one at
``#person/<name>``; 일정·할 일·근거·기록 rows link there, the project to its page, the subject to the source."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import person_href, project_href, render_dashboard, todo_href
from automation.project_board.models import proposal_from_dict

TODAY = date(2026, 10, 14)


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    for pid, project, titles in (("pb-1", "프로젝트A", ("보고서 제출", "일정 취합")), ("pb-2", "프로젝트B", ("발표 준비",))):
        store.apply_proposal(pid, proposal_from_dict({
            "project": project, "summary": "메일 요약", "grade": "공개",
            "todos": [{"title": title, "owner": "가상담당", "due": "2026-10-20"} for title in titles],
            "source": {"kind": "mail", "source_id": f"{pid}-u", "subject": f"{project} 요청", "sender": "가상보낸이"},
        }), message_id=pid)
    return store


def _section(page: str, tab: str, key: str) -> str:
    return page.split(f'<section data-tab="{tab}" data-key="{key}"', 1)[1].split("</section>", 1)[0]


def _schedule_rows(page: str) -> list[str]:
    panel = page.split('<section id="schedule"', 1)[1].split("</section>", 1)[0]
    return re.findall(r"<li data-project=.*?</li>", panel, re.S)


def test_a_schedule_row_links_its_title_project_owner_and_source(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token="t" * 40)
    row = next(r for r in _schedule_rows(page) if "보고서 제출" in r)
    assert f'<a class="ag-title" href="{todo_href("pb-1-1")}">보고서 제출</a>' in row
    assert f'href="{project_href("프로젝트A")}"' in row and f'href="{person_href("가상담당")}"' in row
    assert 'href="/source/mail/pb-1-u"' in row


def test_every_todo_and_person_link_has_its_one_screen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_archived("프로젝트B", True, by="시험")
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token="t" * 40)
    screens = set(re.findall(r'<section data-tab="(?:todo|person)" data-key="([^"]+)"', page))
    todo_links = {m for m in re.findall(r'href="#todo/([^"]+)"', page)}
    person_links = {m for m in re.findall(r'href="#person/([^"]+)"', page)}
    assert todo_links == {"pb-1-1", "pb-1-2", "pb-2-1"}  # the archived project's todo has its screen too
    assert todo_links | {"%EA%B0%80%EC%83%81%EB%8B%B4%EB%8B%B9"} == {*todo_links, *person_links}
    assert {"pb-1-1", "pb-1-2", "pb-2-1", "가상담당"} == screens
    assert len(re.findall(r'<section data-tab="todo" data-key="pb-1-1"', page)) == 1  # one screen, not a copy per list


def test_a_todo_screen_says_when_who_where_why_and_what_came_with_it(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token="t" * 40)
    screen = _section(page, "todo", "pb-1-1")
    assert "10월\u00a020일" in screen and f'href="{person_href("가상담당")}"' in screen
    assert f'href="{project_href("프로젝트A")}"' in screen and 'href="/source/mail/pb-1-u"' in screen
    assert "가상보낸이" in screen and "메일 요약" in screen
    assert f'href="{todo_href("pb-1-2")}"' in screen  # the other todo from the same mail
    assert re.findall(r'data-set-status="([^"]+)"', screen) == ["열림", "진행", "완료"]


def test_the_mailed_copy_has_the_screens_but_no_source_links_or_buttons(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="file")
    assert all('class="ag-title" href="#todo/' in r for r in _schedule_rows(page))
    assert "/source/" not in page and "data-set-status" not in page

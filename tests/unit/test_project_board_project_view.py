"""Per-project view: a project card opens ``#project/<name>`` with only that project's rows,
and the 할 일·일정 tabs carry a 전체/프로젝트 picker whose pills are links (back and sharing work)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import project_href, render_dashboard
from automation.project_board.models import proposal_from_dict

TODAY = date(2026, 10, 14)


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    for pid, project, grade, titles in (
        ("pb-1", "프로젝트A", "대외비", ("보고서 제출", "일정 취합")),
        ("pb-2", "프로젝트B", "공개", ("발표 준비",)),
    ):
        store.apply_proposal(pid, proposal_from_dict({
            "project": project, "summary": "요약", "grade": grade,
            "todos": [{"title": title, "due": "2026-10-20"} for title in titles],
            "source": {"kind": "mail", "source_id": f"{pid}-u", "subject": f"{project} 요청"},
        }), message_id=pid)
    return store


def _page_of(page: str, project: str) -> str:
    match = re.search(rf'<section id="pv-\d+" data-tab="project" data-project="{project}">(.*?)</section>', page, re.S)
    assert match, project
    return match.group(1)


def test_each_project_page_holds_only_its_own_todos_schedule_and_evidence_with_grades(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token="t" * 40)
    a = _page_of(page, "프로젝트A")
    assert "보고서 제출" in a and "일정 취합" in a and "프로젝트A 요청" in a
    assert "발표 준비" not in a and "프로젝트B 요청" not in a
    assert "대외비 · 외부 비공개" in a.split('<div class="pv-head">', 1)[1].split("</div>", 1)[0]  # the grade badge stays on the project screen
    assert 'data-todo="pb-1-1"' in a  # progress can be ticked from the project screen too
    b = _page_of(page, "프로젝트B")
    assert "발표 준비" in b and "보고서 제출" not in b and "대외비" not in b
    ids = re.findall(r'\bid="([^"]+)"', page)
    assert len(ids) == len(set(ids))  # the project copy of a todo must not steal its #todo-<id> anchor


def test_summary_and_project_cards_link_to_the_project_page(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="file")
    summary = page.split('<section id="summary"', 1)[1].split("</section>", 1)[0]
    href = "#project/" + quote("프로젝트A", safe="")
    assert project_href("프로젝트A") == href
    assert f'href="{href}"' in summary
    projects = page.split('<section id="projects"', 1)[1].split("</section>", 1)[0]
    assert f'href="{href}"' in projects


def test_todos_and_schedule_tabs_offer_a_project_picker_that_tags_every_row(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token="t" * 40)
    for tab in ("todos", "schedule"):
        panel = page.split(f'<section id="{tab}"', 1)[1].split("</section>", 1)[0]
        pills = re.findall(r'<a href="([^"]+)" data-p="([^"]*)">', panel)
        assert pills[0] == (f"#{tab}", "")
        assert (project_href("프로젝트B", tab), "프로젝트B") in pills
        rows = re.findall(r'<li [^>]*data-project="([^"]+)"', panel)
        assert sorted(set(rows)) == ["프로젝트A", "프로젝트B"] and len(rows) == 3

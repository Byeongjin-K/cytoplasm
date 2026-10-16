"""Dashboard tabs: one section per menu item, chosen by #hash; without JS every section stays open."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import EMPTY_SECTION, TABS, render_dashboard
from automation.project_board.models import proposal_from_dict

TODAY = date(2026, 10, 14)


def test_every_menu_item_owns_exactly_one_panel_and_none_is_hidden_in_the_markup(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "todos": [{"title": "보고서 제출", "due": "2026-10-20"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "요청"},
    }), message_id="m1")
    store.record_progress("pb-1-1", status="진행")
    for mode in ("served", "file"):
        page = render_dashboard(store.snapshot(today=TODAY), mode=mode)  # type: ignore[arg-type]
        assert re.findall(r'<a href="#(\w+)" data-tab="\1">', page) == [tab for tab, _ in TABS]
        panels = re.findall(r'<section id="(\w+)" data-tab="(\w+)"', page)
        assert [tab for _, tab in panels if _ != "events"] == [tab for tab, _ in TABS]
        assert ("events", "summary") in panels
        assert " hidden" not in page.split('<main id="main">', 1)[1].split("<script>", 1)[0]
        assert "window.addEventListener('hashchange'" in page


def test_an_empty_board_says_where_each_tab_fills_from(tmp_path: Path) -> None:
    page = render_dashboard(BoardStore(tmp_path / "board.db").snapshot(today=TODAY), mode="file")
    for tab in ("projects", "todos", "schedule", "sources"):
        panel = page.split(f'<section id="{tab}"', 1)[1].split("</section>", 1)[0]
        assert EMPTY_SECTION in panel
    assert "hashchange" in page

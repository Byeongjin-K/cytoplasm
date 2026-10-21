"""The dashboard's design contract: one token set for both appearances, no stray styling, plain sentences."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from automation.project_board import dashboard_theme
from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import _to, render_dashboard
from automation.project_board.models import PendingView, proposal_from_dict

TODAY = date(2026, 10, 16)


def _page(tmp_path: Path, mode: str) -> str:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "가상과제 알파", "summary": "요약",
        "todos": [{"title": "중간 보고", "owner": "담당자1", "due": "2026-10-14"}, {"title": "시험 준비", "due": "2026-10-20"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "자료 요청", "attachments": ["양식.hwpx"]},
    }), message_id="m1")
    store.record_progress("pb-1-2", status="진행", progress=40, by="owner@example.com")
    pending = (PendingView("pb-9", "가상과제 베타", 2, "새 요청", "2026-10-16T00:00:00Z"),)
    return render_dashboard(store.snapshot(today=TODAY, pending=pending), mode=mode)  # type: ignore[arg-type]


def test_every_colour_and_size_comes_from_a_token_the_dark_appearance_overrides_only_existing_ones() -> None:
    light, dark = re.split(r"@media \(prefers-color-scheme:dark\)", dashboard_theme._TOKENS)
    defined = set(re.findall(r"(--[\w-]+):", light))
    assert set(re.findall(r"(--[\w-]+):", dark)) <= defined
    used = set(re.findall(r"var\((--[\w-]+)\)", dashboard_theme.CSS))
    assert used <= defined, used - defined
    rules = dashboard_theme._BASE + dashboard_theme._PARTS + dashboard_theme._RESPONSIVE
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", rules)  # colours live in the token block only


def test_no_row_or_link_gives_up_its_own_height_or_hides_its_text() -> None:
    """Safari drew the 보안 등급 badge and a wrapped 근거 subject on one spot (2026-10-21): WebKit lifts a
    baseline-aligned grid row holding a multi-line inline-flex link into the row above. Rows align to their
    top, and a mail subject wraps instead of ending in an ellipsis. layout_audit.mjs measures the screens."""
    rules = dict(re.findall(r"(?m)^([^{}@\n]+)\{([^{}]*)\}", dashboard_theme._PARTS))
    assert "align-items:start" in rules[".td-facts"]
    assert all("baseline" not in body for sel, body in rules.items() if ".td-facts" in sel)
    assert all("ellipsis" not in body and "nowrap" not in body for sel, body in rules.items() if ".src-link" in sel)


def test_a_separator_rides_with_the_link_before_it(tmp_path: Path) -> None:
    """A link drawn inline-block leaves a break after it that a no-break space does not close: Safari began the
    next line with 「·」. Every separator after a link sits inside the same no-wrap span as that link."""
    page = _page(tmp_path, "served")
    assert '<span class="glue"><a class="who-link"' in page or '<span class="glue"><a href="#project/' in page
    assert not re.search(r"</a>[\u00a0 ]· ", page)
    rules = dict(re.findall(r"(?m)^([^{}@\n]+)\{([^{}]*)\}", dashboard_theme._PARTS))
    assert rules[".glue"] == "white-space:nowrap"


def test_a_middle_dot_in_a_title_never_begins_a_line_and_search_still_finds_it(tmp_path: Path) -> None:
    """Chrome broke 「조직문화확산·법정의무교육·연구윤리교육」 before a dot under keep-all; a word joiner holds the dot to the word before it."""
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "가상과제 알파", "summary": "요약", "todos": [{"title": "법정의무교육·연구윤리교육 이수", "due": "2026-10-20"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "조직문화확산·법정의무교육 안내"},
    }), message_id="m1")
    page = render_dashboard(store.snapshot(today=TODAY), mode="served")
    body = page.split("<body>", 1)[1].split("<script>", 1)[0]
    assert "법정의무교육⁠·연구윤리교육" in body and "조직문화확산⁠·법정의무교육" in body
    assert not re.search(r"[^\u2060]·연구", re.sub(r"<[^>]*>", "", body))  # visible text, not attributes
    assert "li.textContent.replace(/\\u2060/g,'')" in page  # search matches what the owner types


@pytest.mark.parametrize("mode", ["served", "file"])
def test_the_page_has_no_inline_styles_dashes_or_emoji(tmp_path: Path, mode: str) -> None:
    page = _page(tmp_path, mode)
    body = page.split("<body>", 1)[1].split("<script>", 1)[0]
    assert set(re.findall(r'style="([^"]*)"', body)) <= {"display:none"}  # only the script-toggled schedule notice
    assert "—" not in body and "✅" not in body
    assert '<link rel="icon" href="data:image/svg+xml;base64,' in page


def test_every_checkbox_sits_in_a_44px_label_and_search_has_a_visible_label(tmp_path: Path) -> None:
    page = _page(tmp_path, "served")
    boxes = re.findall(r'<input type="checkbox"', page)
    assert boxes and len(re.findall(r'<label class="check"><input type="checkbox"', page)) == len(boxes)
    assert re.search(r"\.check\{[^}]*width:var\(--hit\);height:var\(--hit\)", dashboard_theme.CSS)
    assert '<label class="field field-search">검색' in page


def test_recent_records_read_as_sentences(tmp_path: Path) -> None:
    page = _page(tmp_path, "served")
    events = page.split('<section id="events"', 1)[1].split("</section>", 1)[0]
    assert "「시험 준비」</span></a> 진행으로 바꿈" in events and "→" not in events
    assert '<div class="who">owner@example.com</div>' in events
    assert "owner@example.com" not in _page(tmp_path / "f", "file")


@pytest.mark.parametrize(("word", "expected"), [("완료", "완료로"), ("진행", "진행으로"), ("열림", "열림으로"), ("보류", "보류로"), ("달", "달로")])
def test_the_particle_follows_the_last_syllable(word: str, expected: str) -> None:
    assert _to(word) == expected


def test_a_local_check_reads_as_this_device_and_never_shows_the_address(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "가상과제 알파", "summary": "요약", "todos": [{"title": "중간 보고"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "자료 요청"},
    }), message_id="m1")
    store.record_progress("pb-1-1", status="완료", by="노드 로컬 접속(127.0.0.1)")
    store.set_archived("가상과제 알파", True, by="노드 로컬 접속(127.0.0.1)")
    page = render_dashboard(store.snapshot(today=TODAY), mode="served")
    assert "이 기기에서" in page and "127.0.0.1" not in page and "노드 로컬 접속" not in page

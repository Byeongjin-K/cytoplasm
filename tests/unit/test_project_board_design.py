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
    assert "「시험 준비」 진행으로 바꿈" in events and "→" not in events
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

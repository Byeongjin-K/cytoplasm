"""A todo screen says when, where and why (owner 2026-10-21 "일정 탭에서도 해당 일정 내용을 클릭해서 볼수가 없어"):
the analysed proposal's time, place and due reason reach the screen, the calendar opens on that day, and the 세션 tab's
summary cards and its "먼저 볼 것" list lead to the groups below."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from automation.project_board import demo
from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import _sender, render_dashboard
from automation.project_board.models import TodoExtra, proposal_from_dict
from automation.project_board.sessions_view import render_sessions

TODAY = date(2026, 10, 14)


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약",
        "todos": [{"title": "착수 회의", "due": "2026-10-20"}, {"title": "기한 없는 일"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "요청"},
    }), message_id="pb-1")
    return store


def _screen(page: str, key: str) -> str:
    return page.split(f'<section data-tab="todo" data-key="{key}"', 1)[1].split("</section>", 1)[0]


def test_the_proposal_keeps_a_clock_time_and_a_place_and_drops_a_malformed_time() -> None:
    raw = {"project": "P", "summary": "s", "source": {"kind": "mail", "source_id": "x", "subject": "s"},
           "todos": [{"title": "회의", "time": "14:30", "place": "본관 3층"}, {"title": "행사", "time": "오후 2시"}]}
    todos = proposal_from_dict(raw).todos
    assert (todos[0].time, todos[0].place) == ("14:30", "본관 3층") and todos[1].time == ""
    assert proposal_from_dict(proposal_from_dict(raw).to_dict()).todos == todos  # the frozen copy round-trips


def test_time_place_reason_and_the_calendar_day_reach_the_todo_screen(tmp_path: Path) -> None:
    extras = {"pb-1-1": TodoExtra("원문 기한", "14:00", "본관 회의실")}
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token="t" * 40, extras=extras)
    screen = _screen(page, "pb-1-1")
    assert "14:00" in screen and "<dt>장소</dt><dd>본관 회의실</dd>" in screen and "<dt>마감 근거</dt><dd>원문 기한</dd>" in screen
    assert 'href="https://calendar.google.com/calendar/r/day/2026/10/20"' in screen
    assert "<dt>장소</dt>" not in _screen(page, "pb-1-2") and "calendar.google.com" not in _screen(page, "pb-1-2")
    mailed = render_dashboard(_store(tmp_path / "f").snapshot(today=TODAY), mode="file", extras=extras)
    assert "calendar.google.com" not in mailed  # the mailed copy stays offline


def test_session_cards_and_the_look_first_list_open_their_groups() -> None:
    page = render_sessions(demo.sessions(demo.datetime.now(demo.UTC)), now_ms=0, exported=False)
    assert re.findall(r'<a class="card ss-pillar" href="(#ss-\w+)">', page) == ["#ss-lanes", "#ss-agents", "#ss-services"]
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    assert {"ss-lanes", "ss-agents", "ss-services"} <= ids
    targets = re.findall(r'<div class="callout ss-attention">.*?</div></div>', page, re.S)[0]
    assert all(href[1:] in ids for href in re.findall(r'<a href="(#[^"]+)"', targets))


def test_a_quoted_mail_sender_reads_as_a_name_and_address() -> None:
    assert _sender('\\"가상인물\\" <a@example.org>') == "가상인물 <a@example.org>"  # as stored from a real header
    assert _sender('"가상인물" <a@example.org>') == "가상인물 <a@example.org>"
    assert _sender("연구관리팀") == "연구관리팀"

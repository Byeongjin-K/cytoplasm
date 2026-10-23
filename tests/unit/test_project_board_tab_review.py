"""Tab review (owner Q360, 2026-10-23): the ledger tab is gone and 도리 지시 holds its slot, every row on a tab leads
somewhere, a schedule row changes its status in place, and a chip never points at a file the page cannot hand over."""

from __future__ import annotations

import http.client
import re
import threading
from datetime import date
from pathlib import Path

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import render_dashboard
from automation.project_board.models import proposal_from_dict
from automation.project_board.orders import OrderStore
from automation.project_board.server import make_server

TODAY = date(2026, 10, 23)
TOKEN = "t" * 40


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "todos": [
            {"title": "보고서 제출", "due": "2026-10-24"}, {"title": "자료 정리", "due": "2026-10-30"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "자료 요청",
                   "attachments": ["양식.hwpx", "Download all", "Save"]},
    }), message_id="m1")
    store.record_progress("pb-1-2", status="진행")
    return store


def test_orders_take_the_seventh_tab_on_the_live_page_and_the_ledger_tab_is_gone(tmp_path: Path) -> None:
    # Given: a board with an orders store, rendered live and as the exported file.
    store = _store(tmp_path)
    orders = OrderStore(tmp_path / "orders.db")
    snapshot = store.snapshot(today=TODAY)
    served = render_dashboard(snapshot, mode="served", orders=orders.all())
    exported = render_dashboard(snapshot, mode="file")
    # Then: the live nav ends with 도리 지시 in seven columns; nothing anywhere names the ledger.
    nav = served.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'data-tab="(\w+)"', nav) == ["summary", "projects", "todos", "schedule", "sources", "sessions", "orders"]
    assert 'class="seven"' in nav and "도리 지시" in nav
    assert "ledger" not in served and "장부" not in served
    # And: the file keeps six tabs and none of the owner's orders.
    file_nav = exported.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert "orders" not in file_nav and 'id="orders"' not in exported
    # And: the orders tab hides its back link until a todo or project screen opens it.
    assert re.search(r'<section id="orders"[^>]*><a class="back" href="#summary" data-back hidden>', served)


def test_a_schedule_row_changes_its_status_through_the_todo_save(tmp_path: Path) -> None:
    snapshot = _store(tmp_path).snapshot(today=TODAY)
    served = render_dashboard(snapshot, mode="served")
    schedule = served.split('<section id="schedule"', 1)[1].split("</section>", 1)[0]
    # Then: each open row has the status field, its current value chosen, posting to the same /todo save.
    picks = re.findall(r'<select data-status-todo="([^"]+)" data-was="([^"]+)"[^>]*>(.*?)</select>', schedule)
    assert {(todo, was) for todo, was, _ in picks} == {("pb-1-1", "열림"), ("pb-1-2", "진행")}
    for _, was, options in picks:
        assert re.findall(r'<option value="([^"]+)"', options) == ["열림", "진행", "완료"]
        assert f'<option value="{was}" selected>' in options
    assert "post('/todo/'+encodeURIComponent(sel.dataset.statusTodo)" in served
    # And: the exported file is read-only.
    exported = render_dashboard(snapshot, mode="file")
    assert "data-status-todo" not in exported.split('<section id="schedule"', 1)[1].split("</section>", 1)[0]


def test_a_todo_group_header_folds_anywhere_and_the_project_is_its_own_button(tmp_path: Path) -> None:
    served = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served")
    group = served.split('<section id="todos"', 1)[1].split('class="card fold todo-group"', 1)[1].split("</summary>", 1)[0]
    title = re.search(r'<span class="fold-title">(.*?)</span>', group)
    assert title is not None and "<a" not in title.group(1)
    assert '<a class="btn fold-go" href="#project/' in group


def test_a_project_screen_back_link_returns_to_the_screen_that_opened_it(tmp_path: Path) -> None:
    served = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served")
    page = served.split('data-tab="project" data-project="프로젝트A"', 1)[1]
    assert page.startswith('><a class="back" href="#projects" data-back>')


def test_an_attachment_chip_shows_only_for_a_file_the_page_can_hand_over(tmp_path: Path) -> None:
    # Given: the mail recorded two webmail buttons as attachments next to the real file.
    store = _store(tmp_path)
    stored = tmp_path / "stored"
    stored.mkdir()
    (stored / "양식.hwpx").write_bytes(b"x")
    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=store, today=lambda: TODAY, pending=lambda: (),
        read_source=lambda *a: None, on_change=lambda: None,
        attachment_path=lambda kind, source_id, name: stored / name if name == "양식.hwpx" else None,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", int(server.server_address[1]), timeout=5)
        connection.request("GET", "/", headers={"Cookie": f"pb_token={TOKEN}"})
        page = connection.getresponse().read().decode("utf-8")
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    # Then: the real file is a link; the buttons read as files are not shown at all.
    assert "/attachment/%EC%96%91%EC%8B%9D.hwpx" in page
    assert "Download all" not in page and ">Save<" not in page


def test_a_stored_mail_note_reads_from_its_subject_not_its_record(tmp_path: Path) -> None:
    # Given: a stored mail note that opens with its record block instead of a heading.
    note = '---\nuid: "u-1"\nfolder: "inbox"\nsubject: "자료 요청"\n---\n\n본문 첫 줄\n'
    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=_store(tmp_path), today=lambda: TODAY, pending=lambda: (),
        read_source=lambda kind, source_id: note, attachment_path=lambda *a: None, on_change=lambda: None,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", int(server.server_address[1]), timeout=5)
        connection.request("GET", "/source/mail/u-1", headers={"Cookie": f"pb_token={TOKEN}"})
        page = connection.getresponse().read().decode("utf-8")
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    # Then: the reader shows the subject as its title and the body, never the uid or folder lines.
    assert "<h1>자료 요청</h1>" in page and "본문 첫 줄" in page
    assert "uid:" not in page and "folder:" not in page

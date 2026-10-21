"""Project archive (보관) and project order (2026-10-15 owner ask).

An archived project leaves every main screen — 요약, 프로젝트, 할 일, 일정, 근거 and the project
dropdown — and shows only under 보관함. It is a mark, not a deletion: todos and evidence stay,
and who/when is recorded. The order of projects is decided by one function.
"""

from __future__ import annotations

import http.client
import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote

import pytest

from automation.project_board import board_store, dashboard
from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import render_dashboard
from automation.project_board.models import ProjectBoardError, proposal_from_dict
from automation.project_board.server import make_server

TODAY = date(2026, 10, 15)
TOKEN = "t" * 40
NOW = datetime(2026, 10, 15, 6, 30, tzinfo=UTC)
SEEDED = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)
ORIGIN = "https://board-host.example-tailnet.ts.net:8810"
OWNER = "owner@example.com"


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    for pid, project, title in (("pb-1", "가상과제 감마", "감마 보고"), ("pb-2", "가상과제 알파", "알파 보고"),
                                ("pb-3", "가상과제 베타", "베타 보고")):
        store.apply_proposal(pid, proposal_from_dict({
            "project": project, "summary": "요약", "todos": [{"title": title, "due": "2026-10-20"}],
            "source": {"kind": "mail", "source_id": f"{pid}-u", "subject": f"{project} 메일"},
        }), message_id=pid, now=SEEDED)
    return store


def _section(page: str, tab: str) -> str:
    return page.split(f'<section id="{tab}"', 1)[1].split("</section>", 1)[0]


def _picked(page: str, tab: str) -> list[str]:
    return re.findall(r'<option value="[^"]+" data-p="([^"]+)">', _section(page, tab))


def test_archived_project_leaves_every_main_screen_and_shows_only_in_the_shelf(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.set_archived("가상과제 감마", True, by="owner@example.com", now=NOW) is True
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token=TOKEN)
    for tab in ("summary", "projects", "todos", "schedule", "sources"):
        assert "가상과제 감마" not in _section(page, tab), tab
    assert "감마 보고" not in _section(page, "todos") and "알파 보고" in _section(page, "todos")
    shelf = _section(page, "archive")
    assert "가상과제 감마" in shelf and "가상과제 알파" not in shelf
    assert "owner@example.com" in shelf and "10월 15일 15:30" in shelf  # who and when, on the owner's screen
    archived_page = re.search(r'<section id="pv-\d+" data-tab="project" data-project="가상과제 감마" data-archived="1">(.*?)</section>',
                              page, re.S)
    assert archived_page and "감마 보고" in archived_page.group(1)  # nothing deleted: its todos still open
    assert 'data-archive="0" data-archive-project="가상과제 감마">보관 해제</button>' in archived_page.group(1)
    assert store.todo("pb-1-1") is not None


def test_archive_is_a_recorded_mark_and_unarchive_brings_it_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.set_archived("가상과제 베타", True, by="  owner@example.com ", now=NOW) is True
    assert store.set_archived("가상과제 베타", True, by="someone", now=NOW) is False  # already archived: unchanged
    snapshot = store.snapshot(today=TODAY)
    [shelved] = snapshot.archived
    assert (shelved.name, shelved.archived_at, shelved.archived_by) == ("가상과제 베타", "2026-10-15T06:30:00Z", "owner@example.com")
    assert [e.kind for e in snapshot.events if e.project == "가상과제 베타"][0] == "archive"
    assert store.set_archived("가상과제 베타", False, by="owner@example.com", now=NOW) is True
    back = store.snapshot(today=TODAY)
    assert not back.archived and "가상과제 베타" in [view.name for view in back.projects]
    with pytest.raises(ProjectBoardError):
        store.set_archived("없는 과제", True, by="owner@example.com")


def test_work_approved_after_archiving_is_flagged_only_inside_the_shelf(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_archived("가상과제 감마", True, by=OWNER)
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token=TOKEN)
    assert '보관함 <span class="badge d-none">1</span></a>' in page and "보관 뒤 새 할 일" not in page
    store.apply_proposal("pb-9", proposal_from_dict({
        "project": "가상과제 감마", "summary": "요약", "todos": [{"title": "보관 뒤 요청"}, {"title": "하나 더"}],
        "source": {"kind": "mail", "source_id": "pb-9-u", "subject": "다시 온 메일"},
    }), message_id="pb-9", now=datetime(2099, 1, 1, tzinfo=UTC))
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token=TOKEN)
    for tab in ("summary", "projects", "todos", "schedule", "sources"):
        assert "가상과제 감마" not in _section(page, tab), tab  # owner: archived projects appear nowhere on the main screens
    assert "보관 뒤 새 할 일 2" in _section(page, "archive")
    assert '보관함 <span class="badge d-none">1</span> <span class="badge d-soon">새 2</span></a>' in page


def test_the_documented_rollback_leaves_exactly_the_v2_columns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_archived("가상과제 감마", True, by=OWNER)
    with sqlite3.connect(tmp_path / "board.db") as db:
        db.executescript(ROLLBACK_SQL)
        rolled = {t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")] for t in ("projects", "events", "todos")}
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    with sqlite3.connect(tmp_path / "v2.db") as db:
        db.executescript(board_store._SCHEMA)
        db.execute("ALTER TABLE todos ADD COLUMN task_key TEXT NOT NULL DEFAULT ''")
        v2 = {t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")] for t in ("projects", "events", "todos")}
    assert rolled == v2


ROLLBACK_SQL = (Path(__file__).resolve().parents[2] / "docs" / "guide" / "프로젝트-대시보드.md").read_text(
    encoding="utf-8").split("```sql\n", 1)[1].split("```", 1)[0]


def test_the_file_copy_does_not_carry_who_archived(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_archived("가상과제 감마", True, by="owner@example.com", now=NOW)
    store.set_archived("가상과제 감마", False, by="owner@example.com", now=NOW)
    store.set_archived("가상과제 베타", True, by="owner@example.com", now=NOW)
    page = render_dashboard(store.snapshot(today=TODAY), mode="file")
    assert "owner@example.com" not in page  # the digest copy can be mailed out
    assert "data-archive=" not in page  # read-only copy: no archive button


def test_project_order_is_one_function(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token=TOKEN)
    names = ["가상과제 감마", "가상과제 베타", "가상과제 알파"]
    assert _picked(page, "todos") == names  # 가나다 until the owner's order is settled
    monkeypatch.setattr(dashboard, "project_sort_key", lambda view: (-ord(view.name[-1]),))
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token=TOKEN)
    reverse = list(reversed(names))
    assert _picked(page, "todos") == reverse and _picked(page, "schedule") == reverse
    todo_groups = re.findall(r'<details data-project="([^"]+)"', _section(page, "todos"))
    assert todo_groups == reverse
    cards = re.findall(r'<a class="card pcard" [^>]*data-project="([^"]+)"', _section(page, "projects"))
    assert cards == reverse


def test_the_served_page_script_keeps_every_string_on_one_line(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token=TOKEN)
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    broken = [line for line in script.splitlines() if len(re.findall(r"(?<!\\)'", line)) % 2]
    assert broken == []  # a raw newline inside a JS string kills every tab, picker and checkbox on the page


def test_a_board_from_the_previous_release_migrates_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "board.db"
    with sqlite3.connect(path) as db:
        db.executescript(board_store._SCHEMA)
        db.execute("ALTER TABLE todos ADD COLUMN task_key TEXT NOT NULL DEFAULT ''")
        db.execute("INSERT INTO projects VALUES ('가상과제 알파', '', 'x', 'x')")
        db.execute("PRAGMA user_version = 2")
    store = BoardStore(path)
    snapshot = store.snapshot(today=TODAY)
    assert [view.name for view in snapshot.projects] == ["가상과제 알파"] and not snapshot.archived
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == board_store.SCHEMA_VERSION == 3


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[int, BoardStore]]:
    store = _store(tmp_path)
    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=store, today=lambda: TODAY, pending=lambda: (),
        read_source=lambda *a: None, attachment_path=lambda *a: None, on_change=lambda: None,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield int(server.server_address[1]), store
    finally:
        server.shutdown()
        server.server_close()


def _post(port: int, path: str, body: str, headers: dict[str, str]) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("POST", path, body=body.encode(), headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", **headers})
    response = connection.getresponse()
    result = response.status, response.read().decode("utf-8")
    connection.close()
    return result


def test_archive_post_needs_the_same_authority_as_a_progress_tick(served: tuple[int, BoardStore]) -> None:
    port, store = served
    path = "/project/" + quote("가상과제 알파", safe="")
    cookie = {"Cookie": f"pb_token={TOKEN}"}
    assert _post(port, path, "archived=1", {})[0] == 401
    assert _post(port, path, "archived=1", cookie)[0] == 403
    assert _post(port, path, "archived=1", {**cookie, "X-PB-Token": TOKEN, "Origin": "https://evil.example"})[0] == 403
    assert _post(port, path, "archived=2", {**cookie, "X-PB-Token": TOKEN})[0] == 400
    assert not store.snapshot(today=TODAY).archived
    status, body = _post(port, path, "archived=1", {**cookie, "X-PB-Token": TOKEN})
    assert status == 200 and json.loads(body) == {"ok": True, "project": "가상과제 알파", "archived": True, "changed": True}
    [shelved] = store.snapshot(today=TODAY).archived
    assert shelved.name == "가상과제 알파" and shelved.archived_by == "노드 로컬 접속(127.0.0.1)"



def test_on_the_tailnet_archive_and_check_record_the_same_tailscale_login(tmp_path: Path) -> None:
    store = _store(tmp_path)
    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=store, today=lambda: TODAY, pending=lambda: (),
        read_source=lambda *a: None, attachment_path=lambda *a: None, on_change=lambda: None,
        tailnet_origins=(ORIGIN,), tailnet_logins=(OWNER,),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = int(server.server_address[1])
        tailnet = {"Host": ORIGIN.removeprefix("https://"), "Tailscale-User-Login": "Owner@Example.com", "Origin": ORIGIN}
        assert _post(port, "/project/" + quote("가상과제 감마", safe=""), "archived=1", tailnet)[0] == 200
        assert _post(port, "/todo/pb-2-1", "status=" + quote("완료"), tailnet)[0] == 200
    finally:
        server.shutdown()
        server.server_close()
    snapshot = store.snapshot(today=TODAY)
    assert snapshot.archived[0].archived_by == OWNER
    assert {(e.kind, e.actor) for e in snapshot.events if e.kind in ("archive", "progress")} == {("archive", OWNER), ("progress", OWNER)}
    served = render_dashboard(snapshot, mode="served", token="")
    assert "「알파 보고」</span></a> 완료로 바꿈" in served and f'<div class="who">{OWNER}</div>' in served
    assert OWNER not in render_dashboard(snapshot, mode="file")

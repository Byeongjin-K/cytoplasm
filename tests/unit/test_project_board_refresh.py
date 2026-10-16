"""The open dashboard refreshes itself: every two minutes, while visible, it asks with the page's ETag.

The server answers 304 when nothing the screen shows has changed. Relative times and the generation
stamp are left out of the tag (the page keeps them current itself), so a later render of the same
data keeps the same tag, while a recorded change gives a new one. The browser side (visibility,
section swap, kept scroll and folds) is checked in a real browser; see docs/guide/프로젝트-대시보드.md.
"""

from __future__ import annotations

import http.client
import threading
from dataclasses import replace
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import render_dashboard, stamp_etag
from automation.project_board.models import proposal_from_dict
from automation.project_board.server import make_server

TODAY = date(2026, 10, 16)
TOKEN = "t" * 40
COOKIE = f"pb_token={TOKEN}"


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "todos": [{"title": "보고서 제출", "due": "2026-10-20"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "요청"},
    }), message_id="m1")
    return store


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


def _get(port: int, headers: dict[str, str]) -> tuple[int, str | None, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/", headers={"Cookie": COOKIE, **headers})
    response = connection.getresponse()
    result = response.status, response.getheader("ETag"), response.read().decode("utf-8")
    connection.close()
    return result


def test_an_unchanged_board_answers_304_and_a_recorded_change_gives_a_new_page(
    served: tuple[int, BoardStore],
) -> None:
    port, store = served
    status, tag, page = _get(port, {})
    assert status == 200 and tag and f'<meta name="pb-etag" content="{tag.replace(chr(34), "&quot;")}">' in page
    assert _get(port, {"If-None-Match": tag})[:2] == (304, tag)
    assert _get(port, {"If-None-Match": f"W/{tag}"})[0] == 304  # a proxy may weaken the tag
    todo = store.snapshot(today=TODAY, pending=()).todos[0]
    store.record_progress(todo.todo_id, status="완료", progress=None, by="test")
    status, changed, page = _get(port, {"If-None-Match": tag})
    assert status == 200 and changed != tag and 'data-todo="' in page


def test_relative_times_and_the_generation_stamp_do_not_change_the_tag(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.snapshot(today=TODAY, pending=())
    later = replace(snapshot, generated_at=(datetime.now(UTC) + timedelta(minutes=9)).isoformat())
    first = stamp_etag(render_dashboard(snapshot, mode="served", token=TOKEN))[0]
    second = stamp_etag(render_dashboard(later, mode="served", token=TOKEN))[0]
    assert first == second


def test_the_mailed_copy_carries_no_refresh(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY, pending=()), mode="file")
    assert 'id="pb-fresh"' not in page and "pb-etag" not in page and "If-None-Match" not in page

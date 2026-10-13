"""The dashboard page and its loopback server: approved data only, local only, token-bound writes."""

from __future__ import annotations

import http.client
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import render_dashboard
from automation.project_board.models import PendingView, ProjectBoardError, proposal_from_dict
from automation.project_board.server import make_server

TODAY = date(2026, 10, 13)
TOKEN = "t" * 40


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약",
        "todos": [
            {"title": "지난 보고서 <script>x</script>", "owner": "담당자1", "due": "2026-10-10"},
            {"title": "이번 주 회의 준비", "due": "2026-10-16"},
            {"title": "다음 달 시험", "due": "2026-11-20"},
            {"title": "기한 없는 정리"},
        ],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "자료 요청", "attachments": ["양식.hwpx"]},
    }), message_id="m1")
    return store


def test_file_mode_is_read_only_offline_and_escaped(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="file")
    assert "/source/" not in page and "data-todo=" not in page and "disabled" in page
    assert "<script>x</script>" not in page and "&lt;script&gt;x&lt;/script&gt;" in page
    assert "http://" not in page and "https://" not in page
    assert "D+3 지연" in page and "D-3" in page and "미정" in page


def test_served_mode_links_sources_and_wires_the_check(tmp_path: Path) -> None:
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token=TOKEN)
    assert 'href="/source/mail/u-1"' in page and "/source/mail/u-1/attachment/" in page
    assert 'data-todo="pb-1-1"' in page and f'content="{TOKEN}"' in page


def test_pending_proposals_are_waiting_not_data(tmp_path: Path) -> None:
    pending = (PendingView("pb-9", "프로젝트B", 3, "새 요청", "2026-10-13T00:00:00Z"),)
    snapshot = _store(tmp_path).snapshot(today=TODAY, pending=pending)
    page = render_dashboard(snapshot, mode="file")
    assert "소유자 승인을 기다리는 제안" in page and "프로젝트B" in page
    assert all(todo.project == "프로젝트A" for todo in snapshot.todos)


def test_empty_board_explains_where_data_comes_from(tmp_path: Path) -> None:
    page = render_dashboard(BoardStore(tmp_path / "b.db").snapshot(today=TODAY), mode="file")
    assert "승인(✅)하면" in page


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[int, BoardStore, list[str], list[str]]]:
    store = _store(tmp_path)
    changes: list[str] = []
    asked: list[str] = []

    def attachment(kind: str, source_id: str, name: str) -> Path | None:
        asked.append(name)
        return None

    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=store, today=lambda: TODAY, pending=lambda: (),
        read_source=lambda kind, source_id: "원문 <b>" if source_id == "u-1" else None,
        attachment_path=attachment, on_change=lambda: changes.append("x"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1]), store, changes, asked
    finally:
        server.shutdown()
        server.server_close()


def _call(port: int, method: str, path: str, *, headers: dict[str, str] | None = None,
          body: str | None = None) -> tuple[int, dict[str, str], str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body.encode() if body else None, headers=headers or {})
    response = connection.getresponse()
    text = response.read().decode("utf-8")
    connection.close()
    return response.status, {k.lower(): v for k, v in response.getheaders()}, text


COOKIE = {"Cookie": f"pb_token={TOKEN}"}
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.0.10"])
def test_non_loopback_bind_is_refused(tmp_path: Path, host: str) -> None:
    with pytest.raises(ProjectBoardError, match="local only"):
        make_server(host=host, port=0, token=TOKEN, store=BoardStore(tmp_path / "b.db"), today=lambda: TODAY,
                    pending=lambda: (), read_source=lambda *a: None, attachment_path=lambda *a: None,
                    on_change=lambda: None)
    with pytest.raises(ProjectBoardError, match="at least"):
        make_server(host="127.0.0.1", port=0, token="short", store=BoardStore(tmp_path / "b.db"),
                    today=lambda: TODAY, pending=lambda: (), read_source=lambda *a: None,
                    attachment_path=lambda *a: None, on_change=lambda: None)


def test_token_is_required_and_the_query_becomes_a_strict_cookie(served: tuple[int, BoardStore, list[str], list[str]]) -> None:
    port, _, _, _ = served
    assert _call(port, "GET", "/")[0] == 401
    assert _call(port, "GET", "/?t=wrong")[0] == 401
    status, headers, _ = _call(port, "GET", f"/?t={TOKEN}")
    assert status == 303 and headers["location"] == "/"
    assert "HttpOnly" in headers["set-cookie"] and "SameSite=Strict" in headers["set-cookie"]
    status, headers, page = _call(port, "GET", "/", headers=COOKIE)
    assert status == 200 and "이번 주 회의 준비" in page and "default-src 'none'" in headers["content-security-policy"]
    status, _, source = _call(port, "GET", "/source/mail/u-1", headers=COOKIE)
    assert status == 200 and "원문 &lt;b&gt;" in source


def test_check_needs_the_token_twice_and_a_same_origin(served: tuple[int, BoardStore, list[str], list[str]]) -> None:
    port, store, changes, _ = served
    body = "status=%EC%99%84%EB%A3%8C"
    assert _call(port, "POST", "/todo/pb-1-2", headers={**COOKIE, **FORM}, body=body)[0] == 403
    evil = {**COOKIE, **FORM, "X-PB-Token": TOKEN, "Origin": "http://evil.example"}
    assert _call(port, "POST", "/todo/pb-1-2", headers=evil, body=body)[0] == 403
    assert (store.todo("pb-1-2") or object()).status == "열림"  # type: ignore[attr-defined]

    good = {**COOKIE, **FORM, "X-PB-Token": TOKEN, "Origin": f"http://127.0.0.1:{port}", "Accept": "application/json"}
    status, _, text = _call(port, "POST", "/todo/pb-1-2", headers=good, body=body)
    assert status == 200 and '"status": "완료"' in text
    assert (store.todo("pb-1-2") or object()).status == "완료" and changes == ["x"]  # type: ignore[attr-defined]


def test_attachment_names_with_paths_never_reach_the_resolver(served: tuple[int, BoardStore, list[str], list[str]]) -> None:
    port, _, _, asked = served
    assert _call(port, "GET", "/source/mail/u-1/attachment/..%2F..%2Fetc%2Fpasswd", headers=COOKIE)[0] == 404
    assert asked == []
    assert _call(port, "GET", "/source/mail/u-1/attachment/%EC%96%91%EC%8B%9D.hwpx", headers=COOKIE)[0] == 404
    assert asked == ["양식.hwpx"]

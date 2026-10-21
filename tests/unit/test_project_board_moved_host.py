"""Saving keeps working after the node moves tailnet host or the server restarts.

2026-10-21: the dashboard moved to another tailnet host, the configured ``dashboard.tailnet.origins``
still named the old one, and every todo tick from the owner's phone came back 403 while the page
itself opened fine. A tokenless tailnet save now also accepts the page's own origin
(``https://`` + the ``Host`` the browser addressed). A server restart rereads the same
``serve.token``, so a page opened before the restart still saves. Kept apart from
``test_project_board_tailnet.py`` so that file's cases stay as they were.
"""

from __future__ import annotations

import http.client
import threading
from datetime import date
from pathlib import Path

from automation.project_board.board_store import BoardStore
from automation.project_board.models import proposal_from_dict
from automation.project_board.server import make_server
from automation.project_board.service import Board

TODAY = date(2026, 10, 21)
OLD_ORIGIN = "https://old-host.example-tailnet.ts.net:8810"
NEW_HOST = "new-host.example-tailnet.ts.net:8810"
OWNER = "owner@example.com"
BODY = "status=%EC%99%84%EB%A3%8C"
FORM = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}


def _store(path: Path) -> BoardStore:
    store = BoardStore(path)
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "todos": [{"title": "보고서 제출", "due": "2026-10-30"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "요청"},
    }), message_id="m1")
    return store


def _serve(store: BoardStore, token: str, *, tailnet_token: bool = False) -> tuple[int, object]:
    server = make_server(
        host="127.0.0.1", port=0, token=token, store=store, today=lambda: TODAY, pending=lambda: (),
        read_source=lambda *a: None, attachment_path=lambda *a: None, on_change=lambda: None,
        tailnet_origins=(OLD_ORIGIN,), tailnet_logins=(OWNER,), tailnet_token=tailnet_token,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return int(server.server_address[1]), server


def _stop(server: object) -> None:
    server.shutdown()  # type: ignore[attr-defined]
    server.server_close()  # type: ignore[attr-defined]


def _call(port: int, method: str, path: str, headers: dict[str, str], body: str | None = None) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body.encode() if body else None, headers=headers)
    status = connection.getresponse().status
    connection.close()
    return status


def _status(store: BoardStore) -> str:
    todo = store.todo("pb-1-1")
    assert todo is not None
    return todo.status


def test_a_moved_host_saves_from_its_own_page_and_still_refuses_other_sites(tmp_path: Path) -> None:
    store = _store(tmp_path / "board.db")
    port, server = _serve(store, "t" * 40)
    owner = {**FORM, "Host": NEW_HOST, "Tailscale-User-Login": OWNER}
    try:
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, "Origin": "https://evil.example"}, BODY) == 403
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, "Origin": "https://other.example-tailnet.ts.net:8810"},
                     BODY) == 403
        assert _call(port, "POST", "/todo/pb-1-1", owner, BODY) == 403
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, "Origin": f"https://{NEW_HOST}",
                                                    "Tailscale-User-Login": "other@example.com"}, BODY) == 403
        assert _call(port, "POST", "/todo/pb-1-1", {**FORM, "Host": NEW_HOST, "Origin": f"https://{NEW_HOST}"},
                     BODY) == 401
        assert _status(store) == "열림"
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, "Origin": f"https://{NEW_HOST}"}, BODY) == 200
        assert _status(store) == "완료"
    finally:
        _stop(server)


def test_with_the_token_on_a_moved_host_needs_the_token_and_its_own_origin(tmp_path: Path) -> None:
    token = "t" * 40
    store = _store(tmp_path / "board.db")
    port, server = _serve(store, token, tailnet_token=True)
    owner = {**FORM, "Host": NEW_HOST, "Tailscale-User-Login": OWNER, "Cookie": f"pb_token={token}"}
    try:
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, "Origin": f"https://{NEW_HOST}"}, BODY) == 403
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, "X-PB-Token": token, "Origin": "https://evil.example"},
                     BODY) == 403
        assert _status(store) == "열림"
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, "X-PB-Token": token, "Origin": f"https://{NEW_HOST}"},
                     BODY) == 200
        assert _status(store) == "완료"
    finally:
        _stop(server)


def test_a_page_opened_before_a_restart_still_saves_after_it(tmp_path: Path) -> None:
    board = Board(tmp_path)
    store = _store(tmp_path / "board.db")
    before = board.token()
    port, server = _serve(store, before)
    _stop(server)
    after = board.token()
    assert after == before
    port, server = _serve(BoardStore(tmp_path / "board.db"), after)
    origin = f"http://127.0.0.1:{port}"
    try:
        held = {**FORM, "Origin": origin, "Cookie": f"pb_token={before}", "X-PB-Token": before}
        assert _call(port, "POST", "/todo/pb-1-1", {**held, "X-PB-Token": "x" * 40}, BODY) == 403
        assert _call(port, "POST", "/todo/pb-1-1", held, BODY) == 200
        assert _status(store) == "완료"
    finally:
        _stop(server)

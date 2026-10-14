"""Tailnet access to the loopback dashboard: an allowlisted Tailscale login, token optional.

`tailscale serve` on another host proxies into the dashboard and overwrites
Tailscale-User-Login with the caller's real identity. Requests under a configured tailnet
origin, or carrying that header, need an allowlisted login; by owner decision that login alone
opens the board unless `dashboard.tailnet.token` asks for the token too.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from automation.project_board.board_store import BoardStore
from automation.project_board.config import load_config
from automation.project_board.models import ProjectBoardError, proposal_from_dict
from automation.project_board.server import make_server

TODAY = date(2026, 10, 13)
TOKEN = "t" * 40
ORIGIN = "https://board-host.example-tailnet.ts.net:8810"
HOST = "board-host.example-tailnet.ts.net:8810"
OWNER = "owner@example.com"
COOKIE = {"Cookie": f"pb_token={TOKEN}"}


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "todos": [{"title": "보고서 제출", "due": "2026-10-20"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "요청"},
    }), message_id="m1")
    return store


def _serve(
    store: BoardStore, origins: tuple[str, ...], logins: tuple[str, ...], *, token: bool = False
) -> tuple[int, object]:
    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=store, today=lambda: TODAY, pending=lambda: (),
        read_source=lambda *a: None, attachment_path=lambda *a: None, on_change=lambda: None,
        tailnet_origins=origins, tailnet_logins=logins, tailnet_token=token,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return int(server.server_address[1]), server


@pytest.fixture(params=[True, False], ids=["token-on", "tokenless"])
def tailnet(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[tuple[int, BoardStore]]:
    store = _store(tmp_path)
    port, server = _serve(store, (ORIGIN,), (OWNER,), token=request.param)
    try:
        yield port, store
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]


def _fetch(port: int, method: str, path: str, headers: dict[str, str], body: str | None = None) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body.encode() if body else None, headers=headers)
    response = connection.getresponse()
    result = response.status, response.read().decode("utf-8", errors="replace")
    connection.close()
    return result


def _call(port: int, method: str, path: str, headers: dict[str, str], body: str | None = None) -> int:
    return _fetch(port, method, path, headers, body)[0]


def test_tailnet_requests_need_the_owner_login(tailnet: tuple[int, BoardStore]) -> None:
    port, _ = tailnet
    owner = {"Host": HOST, "Tailscale-User-Login": OWNER}
    assert _call(port, "GET", "/", {**owner, **COOKIE}) == 200
    assert _call(port, "GET", "/", {**owner, "Tailscale-User-Login": "Owner@Example.com", **COOKIE}) == 200
    assert _call(port, "GET", "/", {"Host": HOST, "Tailscale-User-Login": "other@example.com", **COOKIE}) == 403
    assert _call(port, "GET", "/", {"Host": HOST, **COOKIE}) == 403
    assert _call(port, "GET", "/", {"Tailscale-User-Login": "other@example.com", **COOKIE}) == 403
    assert _call(port, "GET", "/", COOKIE) == 200


def test_the_tailnet_origin_may_tick_a_todo_only_as_the_owner(tailnet: tuple[int, BoardStore]) -> None:
    port, store = tailnet
    form = {"Content-Type": "application/x-www-form-urlencoded", "X-PB-Token": TOKEN, "Origin": ORIGIN,
            "Host": HOST, "Accept": "application/json", **COOKIE}
    body = "status=%EC%99%84%EB%A3%8C"
    assert _call(port, "POST", "/todo/pb-1-1", {**form, "Tailscale-User-Login": "other@example.com"}, body) == 403
    assert (store.todo("pb-1-1") or object()).status == "열림"  # type: ignore[attr-defined]
    assert _call(port, "POST", "/todo/pb-1-1", {**form, "Tailscale-User-Login": OWNER}, body) == 200
    assert (store.todo("pb-1-1") or object()).status == "완료"  # type: ignore[attr-defined]


def test_without_a_tailnet_config_identity_headers_are_refused(tmp_path: Path) -> None:
    port, server = _serve(_store(tmp_path), (), ())
    try:
        assert _call(port, "GET", "/", COOKIE) == 200
        assert _call(port, "GET", "/", {"Tailscale-User-Login": OWNER, **COOKIE}) == 403
        origin = {"Content-Type": "application/x-www-form-urlencoded", "X-PB-Token": TOKEN, "Origin": ORIGIN,
                  **COOKIE}
        assert _call(port, "POST", "/todo/pb-1-1", origin, "status=%EC%99%84%EB%A3%8C") == 403
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]


def test_tailnet_config_is_validated(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"dashboard": {"tailnet": {"origins": [ORIGIN.upper()], "logins": [OWNER]}}}),
                    encoding="utf-8")
    config = load_config({"PROJECT_BOARD_CONFIG": str(path), "HOME": str(tmp_path)})
    assert config.tailnet_origins == (ORIGIN,) and config.tailnet_logins == (OWNER,)
    for bad in ({"origins": ["http://plain.example"]}, {"origins": [ORIGIN + "/path"]}, {"logins": ["no-at"]},
                ["not", "an", "object"]):
        path.write_text(json.dumps({"dashboard": {"tailnet": bad}}), encoding="utf-8")
        with pytest.raises(ProjectBoardError):
            load_config({"PROJECT_BOARD_CONFIG": str(path), "HOME": str(tmp_path)})
    path.write_text(json.dumps({"dashboard": {"tailnet": {"origins": [ORIGIN], "logins": [OWNER], "token": True}}}),
                    encoding="utf-8")
    assert load_config({"PROJECT_BOARD_CONFIG": str(path), "HOME": str(tmp_path)}).tailnet_token is True
    path.write_text(json.dumps({"dashboard": {"tailnet": {"token": "yes"}}}), encoding="utf-8")
    with pytest.raises(ProjectBoardError):
        load_config({"PROJECT_BOARD_CONFIG": str(path), "HOME": str(tmp_path)})


def test_the_owner_login_alone_opens_the_board_by_default(tmp_path: Path) -> None:
    store = _store(tmp_path)
    port, server = _serve(store, (ORIGIN,), (OWNER,))
    owner = {"Host": HOST, "Tailscale-User-Login": OWNER}
    form = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    body = "status=%EC%99%84%EB%A3%8C"
    try:
        status, page = _fetch(port, "GET", "/", owner)
        assert status == 200 and "보고서 제출" in page and TOKEN not in page
        assert _call(port, "GET", "/", {"Host": HOST, "Tailscale-User-Login": "other@example.com"}) == 403
        assert _call(port, "GET", "/", {"Host": HOST}) == 403
        assert _call(port, "GET", "/", {}) == 401
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, **form}, body) == 403
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, **form, "Origin": "https://evil.example"}, body) == 403
        assert _call(port, "POST", "/todo/pb-1-1", {**form, "Host": HOST, "Origin": ORIGIN,
                                                    "Tailscale-User-Login": "other@example.com"}, body) == 403
        assert (store.todo("pb-1-1") or object()).status == "열림"  # type: ignore[attr-defined]
        assert _call(port, "POST", "/todo/pb-1-1", {**owner, **form, "Origin": ORIGIN}, body) == 200
        assert (store.todo("pb-1-1") or object()).status == "완료"  # type: ignore[attr-defined]
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]


def test_the_token_can_be_required_again_on_the_tailnet(tmp_path: Path) -> None:
    port, server = _serve(_store(tmp_path), (ORIGIN,), (OWNER,), token=True)
    try:
        assert _call(port, "GET", "/", {"Host": HOST, "Tailscale-User-Login": OWNER}) == 401
        assert _call(port, "GET", "/", {"Host": HOST, "Tailscale-User-Login": OWNER, **COOKIE}) == 200
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]

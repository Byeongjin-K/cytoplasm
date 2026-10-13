"""Evidence links: the server opens only sources an approved card cites, and only their recorded attachments."""

from __future__ import annotations

import http.client
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import render_dashboard
from automation.project_board.models import proposal_from_dict
from automation.project_board.server import make_server

TODAY = date(2026, 10, 14)
TOKEN = "t" * 40
COOKIE = {"Cookie": f"pb_token={TOKEN}"}
ORIGIN = "https://board-host.example-tailnet.ts.net:8810"
OWNER = {"Host": "board-host.example-tailnet.ts.net:8810", "Tailscale-User-Login": "owner@example.com"}


@pytest.fixture
def served(tmp_path: Path) -> Iterator[int]:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "grade": "대외비", "todos": [{"title": "검토", "due": None}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "설계 검토", "attachments": ["a.pdf"]},
    }), message_id="m1")
    stored = tmp_path / "stored"
    stored.mkdir()
    for name in ("a.pdf", "b.pdf"):
        (stored / name).write_bytes(name.encode())
    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=store, today=lambda: TODAY, pending=lambda: (),
        read_source=lambda kind, source_id: f"원문 {source_id}",
        attachment_path=lambda kind, source_id, name: stored / name,
        on_change=lambda: None, tailnet_origins=(ORIGIN,), tailnet_logins=("owner@example.com",),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def _get(port: int, path: str, headers: dict[str, str]) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def test_only_cited_sources_and_their_recorded_attachments_open(served: int) -> None:
    for headers in (COOKIE, OWNER):
        status, body = _get(served, "/source/mail/u-1", headers)
        assert status == 200 and "원문 u-1" in body.decode()
        assert _get(served, "/source/mail/u-1/attachment/a.pdf", headers) == (200, b"a.pdf")
        assert _get(served, "/source/mail/u-2", headers)[0] == 404
        assert _get(served, "/source/mail/u-1/attachment/b.pdf", headers)[0] == 404
        assert _get(served, "/source/mail/u-2/attachment/a.pdf", headers)[0] == 404
        assert _get(served, "/source/mail/u-1/attachment/..%2Fa.pdf", headers)[0] == 404
    assert _get(served, "/source/mail/u-1", {})[0] == 401
    assert _get(served, "/source/mail/u-1", {**OWNER, "Tailscale-User-Login": "other@example.com"})[0] == 403


def test_the_mailed_copy_points_to_the_dashboard_instead_of_linking(tmp_path: Path) -> None:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "todos": [{"title": "검토", "due": None}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "설계 검토", "attachments": ["a.pdf"]},
    }), message_id="m1")
    page = render_dashboard(store.snapshot(today=TODAY, exported=True), mode="file")
    assert "/source/" not in page and "대시보드(테일넷 주소)의 근거 탭에서 열립니다" in page
    assert "/source/mail/u-1" in render_dashboard(store.snapshot(today=TODAY), mode="served")

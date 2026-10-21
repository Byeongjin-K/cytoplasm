"""Invented demo data, isolated roots, and the actual authenticated HTTP surface."""
from __future__ import annotations

import threading
from datetime import date, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, build_opener

import pytest

from automation.project_board.cli import build_parser, cmd_demo, main
from automation.project_board.dashboard import title_text
from automation.project_board.demo import demo_handlers, demo_token, mail_text, make_demo_board, pending
from automation.project_board.models import ProjectBoardError
from automation.project_board.server import make_server

TODAY = date(2026, 10, 16)


def test_seed_when_fresh_has_active_archived_and_pending_work(tmp_path: Path) -> None:
    # Given a fresh demo root and a fixed Friday.
    store = make_demo_board(tmp_path, TODAY)
    # When the dashboard snapshot is requested.
    snapshot = store.snapshot(today=TODAY, pending=pending(TODAY))
    # Then it covers the demo states without turning pending work into approved work.
    assert len(snapshot.projects) == 4
    assert len(snapshot.archived) == 1
    assert len(snapshot.pending) == 1
    active = {project.name for project in snapshot.projects}
    todos = [todo for todo in snapshot.todos if todo.project in active]
    assert any(todo.due < TODAY.isoformat() and todo.status != "완료" for todo in todos if todo.due)
    sunday = TODAY + timedelta(days=6 - TODAY.weekday())
    assert any(TODAY.isoformat() <= todo.due <= sunday.isoformat() and todo.status != "완료"
               for todo in todos if todo.due)
    assert any(todo.status == "완료" for todo in todos)
    assert all(todo.proposal_id != snapshot.pending[0].request_id for todo in snapshot.todos)


@pytest.mark.parametrize("suffix", ["", "nested"])
def test_seed_when_live_root_or_descendant_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str,
) -> None:
    # Given a sandbox HOME, never the real account's live board.
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".hermes" / "project-board" / suffix
    # When a demo targets the live tree, then no database is created.
    with pytest.raises(ProjectBoardError, match="live board"):
        make_demo_board(root, TODAY)
    assert not (root / "board.db").exists()


def test_seed_when_database_exists_preserves_it(tmp_path: Path) -> None:
    # Given a pre-existing board.
    database = tmp_path / "board.db"
    database.touch()
    # When a demo targets it, then it refuses before writing.
    with pytest.raises(ProjectBoardError, match="already exists"):
        make_demo_board(tmp_path, TODAY)
    assert database.stat().st_size == 0


def test_seed_when_root_is_not_empty_is_refused(tmp_path: Path) -> None:
    # Given a non-empty directory without a database.
    (tmp_path / "keep").mkdir()
    # When a demo targets it, then it refuses without creating a board.
    with pytest.raises(ProjectBoardError, match="empty directory"):
        make_demo_board(tmp_path, TODAY)
    assert not (tmp_path / "board.db").exists()


def test_cli_when_database_exists_returns_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Given an existing board.
    (tmp_path / "board.db").touch()
    # When the demo command targets it.
    result = main(["demo", "--root", str(tmp_path)])
    # Then a single reason is printed.
    assert result == 2
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_demo_when_served_opens_invented_dashboard_and_mail(tmp_path: Path) -> None:
    # Given the real loopback server, demo handlers, and a cookie-aware client.
    store = make_demo_board(tmp_path, TODAY)
    token = demo_token()
    server = make_server(host="127.0.0.1", port=0, token=token, store=store,
                         today=lambda: TODAY, **demo_handlers(lambda: TODAY))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    thread.start()
    try:
        # When the token entry redirects to the dashboard and its cited mail is opened.
        with opener.open(f"{base}/?t={token}", timeout=5) as response:
            assert response.status == 200
            assert response.geturl() == f"{base}/"
            assert "고체전해질 소재 개발" in response.read().decode()
        with opener.open(f"{base}/source/mail/demo-1", timeout=5) as response:
            # Then only the invented source is served.
            assert response.status == 200
            heading, _, body = (mail_text("demo-1") or "").partition("\n")
            page = response.read().decode()
            assert f"<h1>{title_text(heading[2:])}</h1>" in page and body.strip().splitlines()[-1] in page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_parser_when_demo_selected_uses_ephemeral_port() -> None:
    # Given the public CLI parser.
    parser = build_parser()
    # When demo is selected without options.
    args = parser.parse_args(["demo"])
    # Then it dispatches to the demo with independent temporary state.
    assert args.handler is cmd_demo
    assert args.port == 0
    assert args.root is None

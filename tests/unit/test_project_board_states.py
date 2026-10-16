"""The dashboard's states: first use, failures in place, refusal pages in the product's look, and the brand name."""

from __future__ import annotations

import gzip
import http.client
import json
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from automation.project_board.board_store import BoardStore
from automation.project_board.config import load_config
from automation.project_board.dashboard import render_dashboard
from automation.project_board.models import PendingView, ProjectBoardError, proposal_from_dict
from automation.project_board.server import make_server

TODAY = date(2026, 10, 16)
TOKEN = "t" * 40


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "가상과제 알파", "summary": "요약", "todos": [{"title": "중간 보고", "due": "2026-10-20"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "자료 요청"},
    }), message_id="m1")
    return store


def test_an_empty_board_explains_the_three_steps_and_the_demo(tmp_path: Path) -> None:
    page = render_dashboard(BoardStore(tmp_path / "board.db").snapshot(today=TODAY), mode="served", token=TOKEN)
    onboarding = page.split('<div class="card onboard"', 1)[1].split("</ol>", 1)[0]
    assert onboarding.count("<li>") == 3
    assert "python3 -m automation.project_board demo" in page
    assert '<div class="card onboard"' not in render_dashboard(_store(tmp_path / "s").snapshot(today=TODAY), mode="served")


def test_failures_are_shown_in_place_and_archiving_asks_in_a_dialog(tmp_path: Path) -> None:
    served = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="served", token=TOKEN)
    script = served.split("<script>", 1)[1]
    assert "alert(" not in script and "confirm(" not in script
    assert "row-error" in script and "showModal" in script
    assert '<dialog id="archive-dialog"' in served and 'value="cancel">취소</button>' in served
    assert 'role="status" aria-live="polite"' in served
    assert "<dialog" not in render_dashboard(_store(tmp_path / "f").snapshot(today=TODAY), mode="file")


def test_a_pending_proposal_of_an_archived_project_stays_off_the_summary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_archived("가상과제 알파", True, by="owner@example.com")
    pending = (PendingView("pb-9", "가상과제 알파", 1, "새 요청", "2026-10-16T00:00:00Z"),)
    page = render_dashboard(store.snapshot(today=TODAY, pending=pending), mode="served", token=TOKEN)
    summary = page.split('<section id="summary"', 1)[1].split("</section>", 1)[0]
    assert "가상과제 알파" not in summary and "새 요청" not in summary


def test_the_brand_name_comes_from_config_and_is_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setenv("PROJECT_BOARD_CONFIG", str(path))
    assert load_config().brand == "프로젝트 대시보드"
    path.write_text(json.dumps({"dashboard": {"brand": "  가상연구소   과제 현황 "}}), encoding="utf-8")
    assert load_config().brand == "가상연구소 과제 현황"
    for bad in ("  ", "가" * 41, 7):
        path.write_text(json.dumps({"dashboard": {"brand": bad}}), encoding="utf-8")
        with pytest.raises(ProjectBoardError):
            load_config()
    page = render_dashboard(_store(tmp_path).snapshot(today=TODAY), mode="file", brand="가상연구소 과제 현황")
    assert "<title>가상연구소 과제 현황</title>" in page and '<span class="mark" aria-hidden="true">가</span>' in page


@pytest.fixture
def served(tmp_path: Path) -> Iterator[int]:
    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=_store(tmp_path), today=lambda: TODAY, pending=lambda: (),
        read_source=lambda kind, source_id: "원문 <b>" if source_id == "u-1" else None,
        attachment_path=lambda *a: None, on_change=lambda: None, brand="가상연구소 과제 현황",
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def _call(port: int, method: str, path: str, headers: dict[str, str] | None = None) -> tuple[int, str, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, headers=headers or {})
    response = conn.getresponse()
    body = response.read().decode("utf-8")
    conn.close()
    return response.status, response.getheader("Content-Type") or "", body


def test_browser_refusals_are_pages_in_the_product_look_and_the_api_stays_plain(served: int) -> None:
    status, kind, body = _call(served, "GET", "/")
    assert status == 401 and kind.startswith("text/html") and "로그인이 필요합니다" in body and "가상연구소 과제 현황" in body
    assert "?t=" not in body and "token" not in body.split("<body>", 1)[1]
    cookie = {"Cookie": f"pb_token={TOKEN}"}
    status, kind, body = _call(served, "GET", "/nope", cookie)
    assert status == 404 and "찾는 화면이 없습니다" in body and 'href="/"' in body
    status, kind, body = _call(served, "GET", "/source/mail/u-1", cookie)
    assert status == 200 and '<main class="reader">' in body and "원문 &lt;b&gt;" in body and "prefers-color-scheme:dark" in body
    status, kind, body = _call(served, "GET", "/source/mail/u-1/attachment/x.pdf", cookie)
    assert status == 404 and "첨부 파일을 찾을 수 없습니다" in body
    status, kind, _ = _call(served, "POST", "/todo/pb-1-1", {"Content-Length": "0"})
    assert status == 401 and kind.startswith("text/plain")


def test_a_browser_that_accepts_gzip_gets_the_page_compressed(served: int) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", served, timeout=5)
    conn.request("GET", "/", headers={"Cookie": f"pb_token={TOKEN}", "Accept-Encoding": "gzip, br"})
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    assert response.getheader("Content-Encoding") == "gzip" and response.getheader("Vary") == "Accept-Encoding"
    page = gzip.decompress(raw).decode("utf-8")
    assert "가상연구소 과제 현황" in page and len(raw) * 3 < len(page.encode("utf-8"))


def test_icons_are_defined_once_and_long_records_fold_under_more(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for _ in range(5):
        store.record_progress("pb-1-1", status="진행", by="owner@example.com")
        store.record_progress("pb-1-1", status="열림", by="owner@example.com")
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token=TOKEN)
    assert page.count('<symbol id="i-mail"') == 1 and '<use href="#i-mail"/>' in page
    assert "<path" not in page.split("</defs>", 1)[1]
    events = page.split('<section id="events"', 1)[1].split("</section>", 1)[0]
    first, more = events.split('<details class="more">', 1)
    assert first.count("<li>") == 8 and "기록 3건 더 보기" in more and more.count("<li>") == 3

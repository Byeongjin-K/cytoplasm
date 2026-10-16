"""The 세션 tab: the collector's file is read strictly, only labels and numbers reach the page, the mail copy leaves it out."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import render_dashboard
from automation.project_board.sessions import SESSIONS_FILE, load_sessions, parse_sessions
from automation.project_board.sessions_view import EMPTY_SESSIONS, FILE_SESSIONS, STALE_MS, UNREADABLE_SESSIONS

SAMPLE = Path(__file__).resolve().parents[2] / "docs" / "guide" / "세션-현황판" / "sample-data.json"


def _raw() -> dict[str, object]:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _panel(page: str) -> str:
    return page.split('<section id="sessions" data-tab="sessions">', 1)[1].split("</section>", 1)[0]


def _page(tmp_path: Path, sessions: object, *, mode: str = "served", now_ms: int | None = None) -> str:
    now = datetime.fromtimestamp((now_ms or _raw()["now"]) / 1000, UTC)  # type: ignore[operator]
    return render_dashboard(BoardStore(tmp_path / "board.db").snapshot(today=date(2026, 10, 16)), mode=mode,  # type: ignore[arg-type]
                            sessions=sessions, now=now)  # type: ignore[arg-type]


def test_the_sample_renders_every_lane_session_and_service_once(tmp_path: Path) -> None:
    raw = _raw()
    panel = _panel(_page(tmp_path, parse_sessions(raw)))
    counts = dict(re.findall(r'<div class="ss-branch(?: ss-overview)?" data-kind="(\w+)" data-count="(\d+)"', panel))
    assert counts == {"overview": str(len(raw["lanes"]) + len(raw["agents"]) + len(raw["services"])),  # type: ignore[arg-type]
                      "lanes": str(len(raw["lanes"])), "agents": str(len(raw["agents"])), "services": str(len(raw["services"]))}  # type: ignore[arg-type]
    groups = re.findall(r'data-fold="ss-svc-(\w+)" data-count="(\d+)"', panel)
    services = sum(int(n) for _, n in groups)
    assert services == len(raw["services"])  # type: ignore[arg-type]
    assert len(re.findall(r"<li(?: class=\"peak\")?><span class=\"ss-dday\">", panel)) == 2 * 8
    assert 'style="' not in panel and "—" not in panel and "→" not in panel


def test_only_labels_and_numbers_reach_the_page(tmp_path: Path) -> None:
    raw = _raw()
    secret = {"key": "zz-private-lane-key", "title": "홍길동 메일 전달", "prompt": "secret prompt", "path": "/home/someone/x.jsonl"}
    for row in raw["lanes"] + raw["agents"]:  # type: ignore[operator]
        row.update(secret)
    page = _page(tmp_path, parse_sessions(raw))
    for value in secret.values():
        assert value not in page


@pytest.mark.parametrize("text", ["{", "[]", '{"w0": 1, "now": 2}', "BROKEN_DUR"])
def test_a_malformed_file_says_it_could_not_be_read(tmp_path: Path, text: str) -> None:
    if text == "BROKEN_DUR":
        raw = _raw()
        raw["lanes"][0]["dur"] = -5  # type: ignore[index]
        text = json.dumps(raw)
    (tmp_path / SESSIONS_FILE).write_text(text, encoding="utf-8")
    state = load_sessions(tmp_path)
    assert state == "unreadable"
    assert UNREADABLE_SESSIONS in _panel(_page(tmp_path, state))


def test_no_file_yet_shows_where_it_fills_from_and_the_summary_stays_quiet(tmp_path: Path) -> None:
    state = load_sessions(tmp_path)
    page = _page(tmp_path, state)
    assert state == "missing" and EMPTY_SESSIONS in _panel(page)
    assert 'class="card ss-strip"' not in page


def test_the_mail_copy_never_carries_sessions(tmp_path: Path) -> None:
    page = _page(tmp_path, parse_sessions(_raw()), mode="file")
    assert FILE_SESSIONS in _panel(page)
    assert 'class="ss-branch' not in page and 'class="card ss-strip"' not in page


def test_a_late_feed_is_called_out_and_a_fresh_one_is_not(tmp_path: Path) -> None:
    data = parse_sessions(_raw())
    fresh = _panel(_page(tmp_path, data, now_ms=data.now + 5 * 60_000))
    late = _panel(_page(tmp_path, data, now_ms=data.now + STALE_MS + 60_000))
    shown = re.sub(r"<[^>]+>", "", fresh)  # the age sits in its own span so the open page can keep it current
    assert "새로 오지 않았습니다" not in fresh and "5분 전에 받은 집계" in shown
    assert "새로 오지 않았습니다" in late


def test_the_summary_links_to_the_tab_when_sessions_arrived(tmp_path: Path) -> None:
    page = _page(tmp_path, parse_sessions(_raw()))
    summary = page.split('<section id="summary"', 1)[1].split("</section>", 1)[0]
    assert '<a class="card ss-strip" href="#sessions">' in summary

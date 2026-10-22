"""The 세션 tab: the collector's file is read strictly, only labels and numbers reach the page, the mail copy leaves it out."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from automation.project_board import demo
from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import render_dashboard
from automation.project_board.sessions import SESSIONS_FILE, WHY_MAX, load_sessions, parse_sessions
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


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _wait_row(**extra: object) -> dict[str, object]:
    raw = _raw()
    row = next(r for r in raw["lanes"] if r["st"] == "hold")  # type: ignore[union-attr]
    row.update(extra)  # type: ignore[union-attr]
    return raw


def test_a_lane_that_waits_reads_as_waiting_with_what_and_when_and_a_stuck_one_as_stuck(tmp_path: Path) -> None:
    data = demo.sessions(datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    panel = _panel(_page(tmp_path, data, now_ms=data.now))
    shown = _text(panel)
    assert "멈춤" not in shown
    assert re.search(r"대기 아침 다이제스트 화면 확인 10월 16일 \d\d:\d\d부터 대기, 10월 17일 08:00 예정", shown)
    assert "대기 소유자 승인(✅), v2.4.0 릴리스 카드" in shown
    # a group's own line is its start, never a status: a lane parked this morning must not read as waiting since the group began
    starts = re.findall(r'<p class="ss-line">([^<]*)</p>', panel.split('data-kind="lanes"', 1)[1].split('data-kind="agents"', 1)[0])
    assert starts and all("대기" not in s and "진행 중" not in s for s in starts)
    assert any(s.endswith(" 시작") for s in starts) and any(s.endswith("까지") for s in starts)
    assert "막힘 작업 창이 오류로 멈췄습니다" in shown
    assert re.search(r"에이전트 운영 \d+ ?완료 \d+, 진행 \d+, 대기 1", shown)
    assert re.search(r"웹·대시보드 \d+ ?완료 \d+, 진행 \d+, 막힘 1", shown)


def test_an_older_feed_that_still_says_hold_reads_as_waiting(tmp_path: Path) -> None:
    data = parse_sessions(_wait_row())
    assert [x.st for x in data.lanes if x.why] == ["wait"]
    shown = _text(_panel(_page(tmp_path, data)))
    assert "멈춤" not in shown and "대기 소유자 결정, 질문 Q41" in shown


def test_a_time_that_already_passed_says_so(tmp_path: Path) -> None:
    raw = _wait_row()
    data = parse_sessions(raw)
    until = next(x.until for x in data.lanes if x.until)
    assert "시각 지남" not in _text(_panel(_page(tmp_path, data, now_ms=until - 60_000)))
    assert "예정, 시각 지남" in _text(_panel(_page(tmp_path, data, now_ms=until + 60_000)))


def test_a_reason_never_puts_an_id_a_link_or_a_path_on_the_page(tmp_path: Path) -> None:
    leaks = ["1560484156016828457", "https://discord.com/x", "~/.zzangdori/x.env", "/home/a/b", "ebe3345", "a@b.kr"]
    data = parse_sessions(_wait_row(why="카드 " + " ".join(leaks)[: WHY_MAX - 3]))
    panel = _panel(_page(tmp_path, data))
    for leak in leaks:
        assert leak not in panel


@pytest.mark.parametrize("extra", [{"st": "paused"}, {"why": "가" * (WHY_MAX + 1)}, {"why": "줄\n바꿈"}, {"until": -1}])
def test_a_lane_row_outside_the_contract_makes_the_feed_unreadable(extra: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_sessions(_wait_row(**extra))


def _timer_cards(panel: str) -> dict[str, str]:
    timers = panel.split('data-fold="ss-svc-timer"', 1)[1]
    cards = re.findall(r'<li class="ss-member">(.*?)</li>', timers)
    return {re.search(r"<b>([^<]+)</b>", c).group(1): _text(c).replace("\u00a0", " ") for c in cards}  # type: ignore[union-attr]


def test_a_timer_reads_by_how_its_last_run_ended_and_keeps_the_weeks_failures_as_a_line(tmp_path: Path) -> None:
    data = demo.sessions(datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    panel = _panel(_page(tmp_path, data, now_ms=data.now))
    cards = _timer_cards(panel)
    # failed twice earlier this week, fine since: 정상, and the two failures stay on the card
    assert cards["백업"].startswith("백업 매일 정상")
    assert re.search(r"7일간 7번 중 실패 2번, 마지막 실패 10월 14일 \d\d:\d\d$", cards["백업"])
    # its latest run failed: 실패 in red, whatever the rest of the week did
    assert cards["업데이트 확인"].startswith("업데이트 확인 30분마다 실패")
    assert '<span class="badge d-over">실패</span>' in panel
    assert re.search(r"7일간 336번 중 실패 3번, 마지막 실패 10월 16일 \d\d:\d\d$", cards["업데이트 확인"])
    assert cards["아침 점검"].endswith("7일간 7번 실행, 실패 없음")
    assert "실패 있음" not in _text(panel)


def test_a_feed_without_the_last_result_still_shows_the_weeks_failures(tmp_path: Path) -> None:
    raw = _raw()
    for row in raw["services"]:  # type: ignore[union-attr]
        row.pop("lastOk", None)  # type: ignore[union-attr]
        row.pop("lastFail", None)  # type: ignore[union-attr]
    cards = _timer_cards(_panel(_page(tmp_path, parse_sessions(raw))))
    assert cards["상태 확인"].startswith("상태 확인 30분마다 실패 있음 실패 2회")
    assert cards["정기 백업"].startswith("정기 백업 매일 정상")


@pytest.mark.parametrize("extra", [{"lastOk": "yes"}, {"lastOk": 1}, {"lastFail": -1}])
def test_a_service_row_with_a_bad_last_result_makes_the_feed_unreadable(extra: dict[str, object]) -> None:
    raw = _raw()
    next(r for r in raw["services"] if r["group"] == "timer").update(extra)  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        parse_sessions(raw)


def test_the_summary_links_to_the_tab_when_sessions_arrived(tmp_path: Path) -> None:
    page = _page(tmp_path, parse_sessions(_raw()))
    summary = page.split('<section id="summary"', 1)[1].split("</section>", 1)[0]
    assert '<a class="card ss-strip" href="#sessions">' in summary

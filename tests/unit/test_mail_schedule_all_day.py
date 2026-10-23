"""Date-only mail schedules: absent/off keeps today's prompt and re-ask; on drafts all-day.

Every mail text here is invented; no real mail content.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills/mail/scripts"))
sys.path.insert(0, str(REPO / "skills/calendar/scripts"))

import triage_core  # noqa: E402
import triage_digest  # noqa: E402
import triage_llm  # noqa: E402
import triage_transport  # noqa: E402
from automation import mail_schedule_all_day  # noqa: E402

calendar_core = import_module("calendar_core")
calendar_cli = import_module("calendar_cli")
calendar_card = import_module("calendar_card")
calendar_digest = import_module("calendar_digest")
calendar_gate = import_module("calendar_gate")

PROMPT = REPO / "skills/mail/prompts/triage-classify-v1.md"
NOW = datetime(2031, 10, 23, 8, 0, tzinfo=calendar_core.KST)  # Thursday
MAIL = {"subject": "가상 과제 연차평가 안내", "sender": "예시 <a@example.invalid>", "body": "11월 18일 연차평가"}


@pytest.fixture
def hermes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(tmp_path / "absent-peers.yaml"))
    monkeypatch.delenv(mail_schedule_all_day.CALENDAR_ENV, raising=False)
    return home / ".hermes"


def _marker(hermes: Path, value: bytes) -> None:
    (hermes / "mail-schedule-all-day.mode").write_bytes(value)


def _classify_prompt(monkeypatch) -> str:
    seen: list[str] = []

    def served(prompt: str, **_kwargs):
        seen.append(prompt)
        return SimpleNamespace(text=json.dumps({
            "category": "important", "reply_needed": False, "schedule_needed": True,
            "budget": False, "schedule_text": "11월 18일 연차평가", "reason": "test",
        }), provider="stub", model="stub")

    monkeypatch.setattr(triage_llm, "call_codex", served)
    monkeypatch.setattr(triage_llm, "_log_call", lambda **_kwargs: None)
    triage_llm.classify(**MAIL, uid_opaque="u-masked", prompt_path=PROMPT)
    return seen[0]


def _today_prompt() -> str:
    return triage_core.build_prompt(triage_core.load_prompt_template(PROMPT), **MAIL)


# --- mail: the classify prompt ------------------------------------------------

@pytest.mark.parametrize("marker", [None, b"off"])
def test_off_sends_todays_classify_prompt_byte_for_byte(hermes, monkeypatch, marker):
    if marker is not None:
        _marker(hermes, marker)
    assert _classify_prompt(monkeypatch) == _today_prompt()


def test_on_asks_for_date_only_lines_and_changes_nothing_else(hermes, monkeypatch):
    _marker(hermes, b"on")
    received = _classify_prompt(monkeypatch)
    today = _today_prompt()
    # Only the two rule lines the classifier's JSON keys name differ; every other line is today's.
    changed = [(old, new) for old, new in zip(today.splitlines(), received.splitlines(), strict=True)
               if old != new]
    assert [new.split(":", 1)[0] for _old, new in changed] == ["- schedule_needed", "- schedule_text"]
    for current, replacement in mail_schedule_all_day.PROMPT_RULES:
        today = today.replace(current, replacement)
    assert received == today


@pytest.mark.parametrize("marker", [b"ON", b"on\n", b"", b"1"])
def test_broken_marker_keeps_todays_prompt_and_says_so(hermes, monkeypatch, capsys, marker):
    _marker(hermes, marker)
    assert _classify_prompt(monkeypatch) == _today_prompt()
    assert "MAIL-SCHEDULE-ALL-DAY-MARKER-INVALID" in capsys.readouterr().err


def test_on_refuses_a_prompt_without_the_known_rules():
    with pytest.raises(ValueError, match="no known schedule rule"):
        mail_schedule_all_day.apply_to_template("{{SUBJECT}} {{SENDER}} {{BODY}}", "on")
    template = triage_core.load_prompt_template(PROMPT)
    assert mail_schedule_all_day.apply_to_template(template, "off") == template


# --- calendar: parsing --------------------------------------------------------

def test_date_only_without_opt_in_still_reasks():
    with pytest.raises(calendar_core.AmbiguousTime):
        calendar_core.parse_request("11월 18일 연차평가", NOW)


@pytest.mark.parametrize("text,summary,first,last", [
    ("11월 18일 연차평가", "연차평가", "2031-11-18", "2031-11-19"),
    ("2031-11-02 출장계획서 제출 마감", "출장계획서 제출 마감", "2031-11-02", "2031-11-03"),
    ("12월 22일~23일 겨울학교", "겨울학교", "2031-12-22", "2031-12-24"),
    ("12월 30일 ~ 1월 2일 연말 행사", "연말 행사", "2031-12-30", "2032-01-03"),
    ("오늘 보고서 제출 마감", "보고서 제출 마감", "2031-10-23", "2031-10-24"),
])
def test_opt_in_drafts_a_date_only_line_as_all_day(text, summary, first, last):
    parsed = calendar_core.parse_request(text, NOW, all_day_undated=True)
    assert parsed.all_day
    assert parsed.summary == summary
    assert parsed.start == datetime.fromisoformat(f"{first}T00:00:00+09:00")
    assert parsed.end == datetime.fromisoformat(f"{last}T00:00:00+09:00")


def test_opt_in_keeps_timed_lines_timed():
    timed = calendar_core.parse_request("11월 18일 오후 3시 연차평가", NOW, all_day_undated=True)
    assert timed == calendar_core.parse_request("11월 18일 오후 3시 연차평가", NOW)
    assert not timed.all_day


@pytest.mark.parametrize("text,error", [
    ("연차평가", calendar_core.AmbiguousTime),
    ("11월쯤 연차평가", calendar_core.AmbiguousTime),
    ("12월 23일~22일 겨울학교", calendar_core.ParseRejected),
])
def test_opt_in_still_refuses_undated_or_backwards_lines(text, error):
    with pytest.raises(error):
        calendar_core.parse_request(text, NOW, all_day_undated=True)


@pytest.mark.parametrize("text", ["10월 20일 제출 마감", "1월 5일 결과 통보"])
def test_opt_in_does_not_move_a_passed_yearless_day_to_next_year(text):
    # NOW is 2031-10-23: a past 'M월 D일' is a passed deadline, not next year's event.
    with pytest.raises(calendar_core.AmbiguousTime):
        calendar_core.parse_request(text, NOW, all_day_undated=True)


def test_opt_in_refuses_an_explicit_past_year_and_keeps_the_timed_rollover():
    with pytest.raises(calendar_core.ParseRejected):
        calendar_core.parse_request("2031-10-20 제출 마감", NOW, all_day_undated=True)
    timed = calendar_core.parse_request("10월 20일 오후 3시 회의", NOW, all_day_undated=True)
    assert timed.start.year == 2032 and not timed.all_day


@pytest.mark.parametrize("text", [
    "11월 18일부터 25일간 워크숍", "11월 18일부터 3일간 워크숍", "11월 18일부터 3일 동안 워크숍",
    "11월 18일~3일간 워크숍", "11월 18일 2박 3일 워크숍", "11월 18일부터 2주간 워크숍",
])
def test_opt_in_reasks_a_length_instead_of_reading_it_as_an_end_day(text):
    with pytest.raises(calendar_core.AmbiguousTime):
        calendar_core.parse_request(text, NOW, all_day_undated=True)


@pytest.mark.parametrize("text", ["11월 18일 오후 연차평가", "11월 18일 오전 연차평가"])
def test_opt_in_reasks_a_half_day_without_a_clock_time(text):
    with pytest.raises(calendar_core.AmbiguousTime):
        calendar_core.parse_request(text, NOW, all_day_undated=True)


@pytest.mark.parametrize("opt_in", [False, True])
def test_a_timed_midnight_to_midnight_event_keeps_its_times(opt_in):
    parsed = calendar_core.parse_request("내일 자정부터 24시간 서버 점검", NOW, all_day_undated=opt_in)
    assert not parsed.all_day
    body = json.loads(calendar_core.build_create_argv("primary", parsed)[-1])
    assert body["start"]["dateTime"] == "2031-10-24T00:00:00+09:00"
    summary = calendar_core.render_change_summary(
        action="create", summary=parsed.summary, start=parsed.start.isoformat(),
        end=parsed.end.isoformat(), calendar_id="primary", event_id="",
    )
    assert "일시: 2031-10-24 (금) 00:00 ~ 00:00 KST" in summary
    assert triage_digest._event_when(parsed.start.isoformat(), parsed.end.isoformat()) == "10/24(금) 00:00–00:00"


def test_all_day_insert_uses_calendar_dates_with_exclusive_end():
    parsed = calendar_core.parse_request("12월 22일~23일 겨울학교", NOW, all_day_undated=True)
    body = json.loads(calendar_core.build_create_argv("primary", parsed, color_id="2")[-1])
    assert body == {"summary": "겨울학교", "start": {"date": "2031-12-22"},
                    "end": {"date": "2031-12-24"}, "colorId": "2"}


# --- calendar: the draft-create boundary ---------------------------------------

def _draft():
    return calendar_cli._build_create_draft(argparse.Namespace(
        text="11월 18일 연차평가", summary="", calendar="primary", channel_id="dm",
    ))


@pytest.mark.parametrize("env,marker", [("", b"on"), ("1", None), ("1", b"off")])
def test_draft_create_reasks_unless_mail_asks_and_marker_is_on(hermes, monkeypatch, env, marker):
    if env:
        monkeypatch.setenv(mail_schedule_all_day.CALENDAR_ENV, env)
    if marker is not None:
        _marker(hermes, marker)
    with pytest.raises(calendar_core.AmbiguousTime):
        _draft()


def test_draft_create_refuses_all_day_on_a_broken_marker(hermes, monkeypatch):
    monkeypatch.setenv(mail_schedule_all_day.CALENDAR_ENV, "1")
    _marker(hermes, b"yes")
    with pytest.raises(calendar_gate.GateError) as caught:
        _draft()
    assert caught.value.exit_code == 3


def test_all_day_draft_card_names_the_last_day(hermes, monkeypatch, capsys):
    monkeypatch.setenv(mail_schedule_all_day.CALENDAR_ENV, "1")
    _marker(hermes, b"on")
    draft = _draft()
    body = json.loads(draft["argv"][-1])
    assert body["start"]["date"].endswith("-11-18") and body["end"]["date"].endswith("-11-19")
    assert draft["start"].endswith("-11-18T00:00:00+09:00")
    prepared = calendar_card.prepare_draft(draft)
    year = draft["start"][:4]
    # A new card version shows the day; stored v4/v5 cards keep their bytes.
    assert prepared["render_version"] == "6"
    assert f"시작: {year}-11-18 (종일)\n" in prepared["approval_content"]
    assert f"종료: {year}-11-18 (종일)\n" in prepared["approval_content"]
    assert prepared["sha256"] == draft["sha256"]
    assert calendar_digest.event_key(draft)
    import calendar_output

    calendar_output.print_draft(draft)
    out = capsys.readouterr().out
    assert f"일시: {year}-11-18 (" in out and ") 종일\n" in out
    assert calendar_digest.card_line(draft, None).split(" summary=")[0].endswith(
        f"start={year}-11-18 end={year}-11-19")


def test_all_day_draft_with_a_color_uses_the_new_card_version(hermes, monkeypatch):
    monkeypatch.setenv(mail_schedule_all_day.CALENDAR_ENV, "1")
    _marker(hermes, b"on")
    (hermes / "calendar-default-color.mode").write_bytes(b"2")
    draft = _draft()
    assert json.loads(draft["argv"][-1])["colorId"] == "2"
    prepared = calendar_card.prepare_draft(draft)
    assert prepared["render_version"] == "6"
    assert "색: " in prepared["approval_content"] and "(종일)" in prepared["approval_content"]


def test_v6_pending_round_trip(tmp_path):
    from datetime import UTC

    from calendar_pending import PendingConfirm, PendingConfirmStore

    entry = PendingConfirm(
        draft_id="abc123", sha256="digest", dm_channel_id="222", dm_message_id="333",
        created=datetime(2030, 1, 1, tzinfo=UTC), render_version="6",
    )
    store = PendingConfirmStore(tmp_path / "pending.jsonl")
    store.append(entry)
    assert store.load() == (entry,)


def test_timed_draft_digest_line_keeps_datetimes(hermes):
    draft = calendar_cli._build_create_draft(argparse.Namespace(
        text="내일 자정부터 24시간 서버 점검", summary="", calendar="primary", channel_id="dm",
    ))
    line = calendar_digest.card_line(draft, None)
    assert f" start={draft['start']} end={draft['end']} " in line


def test_calendar_without_the_env_never_imports_the_switch_module(monkeypatch):
    monkeypatch.delenv(mail_schedule_all_day.CALENDAR_ENV, raising=False)
    monkeypatch.setitem(sys.modules, "automation.mail_schedule_all_day", None)
    assert calendar_cli._mail_all_day_undated() is False


def test_mail_without_the_switch_module_keeps_todays_prompt(hermes, monkeypatch):
    import mail_schedule_mode

    _marker(hermes, b"on")
    monkeypatch.setitem(sys.modules, "automation.mail_schedule_all_day", None)
    template = triage_core.load_prompt_template(PROMPT)
    assert mail_schedule_mode.mode() == "off"
    assert mail_schedule_mode.classify_template(template) == template


# --- mail: the calendar child and the digest line --------------------------------

def _env_echo_cli(tmp_path: Path) -> Path:
    script = tmp_path / "calendar_cli.py"
    script.write_text(
        "import os, sys\n"
        f"value = os.environ.get({mail_schedule_all_day.CALENDAR_ENV!r}, '<unset>')\n"
        "sys.stderr.write('ENV=' + value)\n"
        "raise SystemExit(5)\n",
        encoding="utf-8",
    )
    return script


@pytest.mark.parametrize("marker,expected", [(None, "<unset>"), (b"off", "<unset>"), (b"on", "1"),
                                             (b"bad", "<unset>")])
def test_transport_asks_the_calendar_for_all_day_only_when_on(hermes, tmp_path, monkeypatch, capfd,
                                                               marker, expected):
    if marker is not None:
        _marker(hermes, marker)
    monkeypatch.setenv("TRIAGE_CALENDAR_CLI", str(_env_echo_cli(tmp_path)))
    proc = triage_transport._run_calendar("11월 18일 연차평가", "u-masked", "")
    assert proc.stderr == f"ENV={expected}"


@pytest.mark.parametrize("start,end,shown", [
    ("2031-11-18", "2031-11-19", "11/18(화) 종일"),
    ("2031-12-22", "2031-12-24", "12/22(월)–12/23(화) 종일"),
    ("2031-11-18T00:00:00+09:00", "2031-11-19T00:00:00+09:00", "11/18(화) 00:00–00:00"),
    ("2031-11-18T15:00:00+09:00", "2031-11-18T16:00:00+09:00", "11/18(화) 15:00–16:00"),
])
def test_digest_line_shows_all_day_events_as_a_day(start, end, shown):
    assert triage_digest._event_when(start, end) == shown


def test_mail_and_calendar_share_one_env_name():
    import mail_schedule_mode

    assert mail_schedule_mode.CALENDAR_ENV == mail_schedule_all_day.CALENDAR_ENV == calendar_cli.ALL_DAY_ENV

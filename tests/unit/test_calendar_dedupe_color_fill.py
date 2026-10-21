"""Same-event check and execution-time default color (owner 2026-10-21, Q62).

A new file so earlier calendar evidence files keep their recorded output. Every Google
call goes through a fake ``subprocess.run``; titles, ids and dates are invented.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/calendar/scripts"))
calendar_cli = import_module("calendar_cli")
calendar_core = import_module("calendar_core")
calendar_card = import_module("calendar_card")
calendar_confirm = import_module("calendar_confirm")
calendar_gate = import_module("calendar_gate")
calendar_preflight = import_module("calendar_preflight")

TITLE = "(금) 예시관 2층 회의실 예시분야 로봇 기술교류회"
SAME = "(금) 예시관 2층 예시분야 로봇 기술교류회"
DAY = "2031-10-24"
CHANNEL = "111111111111111111"
MESSAGE = "222222222222222222"


def _new(name: str):
    """Modules this change adds, imported per test so the old code fails by behaviour, not collection."""
    return import_module(name)


def _event(event_id: str, title: str, start: str, end: str, **extra: object) -> dict[str, object]:
    return {"id": event_id, "summary": title, "status": "confirmed",
            "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


EXISTING = _event("old-1", TITLE, f"{DAY}T10:30:00+09:00", f"{DAY}T11:30:00+09:00",
                  created="2031-10-01T00:00:00Z")


class FakeGws:
    """Answers list/insert/get/patch/delete; each list returns the next queued day."""

    def __init__(self, days: list[list[dict[str, object]]], *, list_rc: int = 0) -> None:
        self.days = days
        self.list_rc = list_rc
        self.calls: list[list[str]] = []
        self.saved: dict[str, object] = {}

    def __call__(self, argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        operation = argv[1:4]
        if operation == ["calendar", "events", "list"]:
            if self.list_rc:
                return subprocess.CompletedProcess(argv, self.list_rc, "", "synthetic list failure")
            items = self.days.pop(0) if self.days else []
            return subprocess.CompletedProcess(argv, 0, json.dumps({"items": items}), "")
        if operation == ["calendar", "events", "insert"]:
            self.saved = json.loads(argv[argv.index("--json") + 1])
            return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "new-1"}), "")
        if operation == ["calendar", "events", "get"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"id": "new-1", "summary": self.saved.get("summary", "")}), "")
        if operation in (["calendar", "events", "patch"], ["calendar", "events", "delete"]):
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        pytest.fail(f"unexpected gws call {argv}")

    def ops(self) -> list[str]:
        return [call[3] for call in self.calls if call[2] == "events"]

    def body(self, operation: str) -> tuple[dict[str, object], dict[str, object]]:
        call = next(call for call in self.calls if call[3] == operation)
        params = json.loads(call[call.index("--params") + 1])
        body = json.loads(call[call.index("--json") + 1]) if "--json" in call else {}
        return params, body


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(tmp_path / "absent-peers.yaml"))
    monkeypatch.setenv("CALENDAR_GWS_BIN", "fake-gws")
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(tmp_path / "no-live"))
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "synthetic-owner")
    monkeypatch.setattr(calendar_confirm, "_api", lambda *_a, **_kw: pytest.fail("real Discord forbidden"))
    (tmp_path / "hermes").mkdir()
    return tmp_path


def _fake(monkeypatch: pytest.MonkeyPatch, *days: list[dict[str, object]], list_rc: int = 0) -> FakeGws:
    fake = FakeGws(list(days), list_rc=list_rc)
    monkeypatch.setattr(calendar_gate.subprocess, "run", fake)
    return fake


def _draft(title: str = SAME, text: str = f"{DAY} 오전 10시 30분 회의") -> dict:
    return calendar_cli._build_create_draft(argparse.Namespace(
        text=text, summary=title, calendar="primary", channel_id="dm",
        origin_channel_id=CHANNEL, origin_message_id=MESSAGE,
    ))


def _approval() -> calendar_gate.Approval:
    return calendar_gate.Approval(ref="synthetic-ref", method="owner_dm_reaction", owner="synthetic-owner")


def _audit(node: Path) -> list[dict]:
    return [json.loads(line) for line in (node / "approvals.jsonl").read_text().splitlines()]


@pytest.mark.parametrize(("first", "second", "same"), [
    (TITLE, SAME, True),                                  # one word apart
    ("[예시] 기술교류회", "기술교류회 (예시대학)", True),     # brackets and an added word
    ("Weekly Sync", "weekly  sync!", True),               # case, spacing, punctuation
    ("주간 회의", "예산 회의", False),                       # same time, different meeting
    ("1차 기술교류회", "2차 기술교류회", False),               # numbers that disagree
    ("301호 회의", "302호 회의", False),
    ("장비 시연", TITLE, False),                    # another meeting at that hour
])
def test_title_similarity_boundaries(first, second, same) -> None:
    assert _new("calendar_dedupe").similar_title(first, second) is same


@pytest.mark.parametrize(("event", "found"), [
    (EXISTING, True),
    (_event("x", "주간 회의", f"{DAY}T10:30:00+09:00", f"{DAY}T11:30:00+09:00"), False),
    (_event("x", TITLE, "2031-10-25T10:30:00+09:00", "2031-10-25T11:30:00+09:00"), False),
    (_event("x", TITLE, f"{DAY}T11:30:00+09:00", f"{DAY}T12:30:00+09:00"), False),
    (_event("x", TITLE, f"{DAY}T11:00:00+09:00", f"{DAY}T12:00:00+09:00"), True),
    (_event("x", TITLE, f"{DAY}T01:30:00Z", f"{DAY}T02:30:00Z"), True),
    ({**EXISTING, "start": {"dateTime": f"{DAY}T10:30:00", "timeZone": "Asia/Seoul"},
      "end": {"dateTime": f"{DAY}T11:30:00", "timeZone": "Asia/Seoul"}}, True),
    ({**EXISTING, "status": "cancelled"}, False),
    ({**EXISTING, "start": {"date": DAY}, "end": {"date": "2031-10-25"}}, False),
])
def test_same_event_needs_date_overlap_and_title(node, event, found) -> None:
    draft = {"start": f"{DAY}T10:30:00+09:00", "end": f"{DAY}T11:30:00+09:00", "summary": SAME}
    assert bool(_new("calendar_dedupe").duplicates(draft, [event])) is found


def test_draft_card_shows_existing_event_and_keeps_hash(node, monkeypatch, capsys) -> None:
    # Given: the calendar already has the event under a slightly different title.
    _fake(monkeypatch, [EXISTING])
    plain = _draft()
    # When
    marked = _new("calendar_dedupe").annotate(plain)
    card = calendar_card.prepare_draft(marked)
    # Then: the mark is outside the content hash and the card says it will not create.
    assert marked["duplicate_of"]["event_id"] == "old-1"
    assert calendar_core.draft_sha256(marked) == plain["sha256"] == marked["sha256"]
    assert card["render_version"] == "6"
    assert f"이미 있음: {TITLE} · {DAY} 10:30~11:30" in card["approval_content"]
    assert "생성 안 함" in card["approval_content"]
    assert f"DUPLICATE-FOUND draft={plain['id']} existing=old-1" in capsys.readouterr().out


def test_draft_card_is_unchanged_when_the_day_cannot_be_read(node, monkeypatch, capsys) -> None:
    _fake(monkeypatch, list_rc=1)
    plain = _draft()
    marked = _new("calendar_dedupe").annotate(plain)
    assert marked == plain
    assert calendar_card.prepare_draft(marked)["render_version"] == "4"
    assert "DUPLICATE-CHECK-UNAVAILABLE" in capsys.readouterr().err


def test_marked_card_never_creates_even_if_the_old_event_is_gone(node, monkeypatch) -> None:
    # Given: a card that showed '이미 있음', and the live day is now empty.
    fake = _fake(monkeypatch, [])
    draft = {**_draft(), "duplicate_of": _new("calendar_dedupe").Existing("old-1", TITLE, EXISTING["start"]["dateTime"],
                                                                  EXISTING["end"]["dateTime"]).record()}
    calendar_gate.persist_draft(draft)
    # When / Then
    with pytest.raises(_new("calendar_dedupe").AlreadyExists):
        calendar_preflight.guarded_execute_draft(draft, _approval())
    assert fake.ops() == []
    sealed = json.loads(calendar_gate._draft_path(draft["id"]).read_text())
    assert sealed["status"] == "duplicate"
    assert _audit(node)[-1]["result"]["status"] == "skipped-duplicate"


def test_owner_command_does_not_create_an_existing_event(node, monkeypatch, capsys) -> None:
    fake = _fake(monkeypatch, [EXISTING])
    monkeypatch.setattr(sys, "argv", [
        "calendar_cli.py", "owner-create", "--text", f"{DAY} 오전 10시 30분 회의", "--summary", SAME,
        "--origin-channel-id", CHANNEL, "--origin-message-id", MESSAGE,
    ])
    assert calendar_cli.main() == 0
    out = capsys.readouterr().out
    assert "insert" not in fake.ops()
    assert "OWNER-COMMAND-ALREADY-EXISTS calendar" in out
    assert "OWNER-COMMAND-WRITTEN" not in out


def test_unreadable_day_refuses_the_insert(node, monkeypatch) -> None:
    fake = _fake(monkeypatch, list_rc=1)
    draft = _draft()
    calendar_gate.persist_draft(draft)
    with pytest.raises(calendar_gate.GateError) as raised:
        calendar_preflight.guarded_execute_draft(draft, _approval())
    assert raised.value.exit_code == 6
    assert "insert" not in fake.ops()


def test_different_meeting_at_the_same_time_is_created(node, monkeypatch) -> None:
    neighbour = _event("other", "장비 시연", f"{DAY}T10:00:00+09:00", f"{DAY}T12:00:00+09:00")
    fake = _fake(monkeypatch, [neighbour], [neighbour])
    draft = _draft()
    calendar_gate.persist_draft(draft)
    assert calendar_preflight.guarded_execute_draft(draft, _approval()) == "new-1"
    assert fake.ops() == ["list", "insert", "get", "list"]


def test_default_color_is_patched_after_insert_without_touching_the_frozen_draft(node, monkeypatch, capsys) -> None:
    # Given: a draft frozen before the node switch, then the switch is on.
    draft = _draft()
    (node / "hermes/calendar-default-color.mode").write_bytes(b"2")
    calendar_gate.persist_draft(draft)
    fake = _fake(monkeypatch, [], [])
    # When
    calendar_preflight.guarded_execute_draft(draft, _approval())
    # Then: insert ran the frozen bytes; one patch carries only the color, no invitations.
    assert fake.ops() == ["list", "insert", "get", "list", "patch"]
    assert "colorId" not in fake.saved
    params, body = fake.body("patch")
    assert params == {"calendarId": "primary", "eventId": "new-1", "sendUpdates": "none"}
    assert body == {"colorId": "2"}
    sealed = json.loads(calendar_gate._draft_path(draft["id"]).read_text())
    assert sealed["argv"] == draft["argv"] and sealed["sha256"] == draft["sha256"]
    assert sealed["color_fill"] == "2"
    assert "COLOR-FILLED" in capsys.readouterr().out
    fill = next(line for line in _audit(node) if line["action"] == "calendar.color_fill")
    assert fill["approval"]["ref"] == "synthetic-ref" and fill["result"]["status"] == "executed"


@pytest.mark.parametrize("marker", [None, b"off"])
def test_no_patch_without_a_node_color(node, monkeypatch, marker) -> None:
    if marker is not None:
        (node / "hermes/calendar-default-color.mode").write_bytes(marker)
    draft = _draft()
    calendar_gate.persist_draft(draft)
    fake = _fake(monkeypatch, [], [])
    calendar_preflight.guarded_execute_draft(draft, _approval())
    assert "patch" not in fake.ops()


def test_no_patch_when_the_draft_already_froze_a_color(node, monkeypatch) -> None:
    (node / "hermes/calendar-default-color.mode").write_bytes(b"2")
    draft = _draft()
    calendar_gate.persist_draft(draft)
    fake = _fake(monkeypatch, [], [])
    calendar_preflight.guarded_execute_draft(draft, _approval())
    assert fake.saved["colorId"] == "2"
    assert "patch" not in fake.ops()


def _ours(**extra: object) -> dict[str, object]:
    return _event("new-1", SAME, f"{DAY}T10:30:00+09:00", f"{DAY}T11:30:00+09:00",
                  created="2031-10-02T00:00:00Z", **extra)


def test_race_keeps_the_earlier_event_and_removes_ours(node, monkeypatch, capsys) -> None:
    (node / "hermes/calendar-default-color.mode").write_bytes(b"2")
    draft = _draft()
    calendar_gate.persist_draft(draft)
    fake = _fake(monkeypatch, [], [EXISTING, _ours()])
    with pytest.raises(_new("calendar_dedupe").AlreadyExists):
        calendar_preflight.guarded_execute_draft(draft, _approval())
    assert fake.ops() == ["list", "insert", "get", "list", "delete"]
    params, _ = fake.body("delete")
    assert params == {"calendarId": "primary", "eventId": "new-1", "sendUpdates": "none"}
    sealed = json.loads(calendar_gate._draft_path(draft["id"]).read_text())
    assert sealed["status"] == "duplicate" and sealed["removed_event"] == "new-1"
    assert "DUPLICATE-REMOVED" in capsys.readouterr().out


def test_race_with_attendees_asks_before_deleting(node, monkeypatch) -> None:
    draft = _draft()
    calendar_gate.persist_draft(draft)
    fake = _fake(monkeypatch, [], [EXISTING, _ours(attendees=[{"email": "guest@example.invalid"}])])
    asked: list[str] = []
    monkeypatch.setattr(_new("calendar_after_insert"), "_ask_delete", lambda _d, event_id: asked.append(event_id) or "card1")
    calendar_preflight.guarded_execute_draft(draft, _approval())
    assert asked == ["new-1"]
    assert "delete" not in fake.ops()


def test_race_leaves_ours_when_the_other_is_newer(node, monkeypatch) -> None:
    draft = _draft()
    calendar_gate.persist_draft(draft)
    newer = {**EXISTING, "created": "2031-10-03T00:00:00Z"}
    fake = _fake(monkeypatch, [], [newer, _ours()])
    calendar_preflight.guarded_execute_draft(draft, _approval())
    assert "delete" not in fake.ops()


def test_watcher_answers_already_exists_under_the_card(node, monkeypatch) -> None:
    reactions = import_module("test_calendar_confirm_reactions")
    watch = reactions.watch
    draft_id = "abc123"
    entry = replace(reactions._entry(), draft_id=draft_id, dm_channel_id="222", channel_id="222", dm_message_id="333")
    store = reactions.PendingConfirmStore(node / "pending-confirms.jsonl")
    store.append(entry)

    class Sealing:
        def confirm(self, entry, _owner):
            calendar_gate.write_json(calendar_gate._draft_path(entry.draft_id), {"id": entry.draft_id, "status": "duplicate"})

        def discard(self, _draft_id):
            pytest.fail("not cancelled")

    notices: list[str] = []
    monkeypatch.setattr(watch, "_notify_result", lambda _d, _r, content, _o="": notices.append(content))
    monkeypatch.setattr(watch.calendar_gate, "list_drafts", lambda: [])
    watch.run_once(
        store=store, owner_id="111", discord=reactions.FakeDiscord({"✅": ({"id": "111", "bot": False},)}),
        commands=Sealing(), draft_sha256=lambda _draft: "sha-123",
        draft_record=lambda _draft: {"id": draft_id, "action": "create", "digest_day": DAY},
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )
    assert len(notices) == 1 and notices[0].startswith("🔁") and "이미 있음" in notices[0]

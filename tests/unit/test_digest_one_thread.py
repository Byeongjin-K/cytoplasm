"""The morning digest is one standard approval thread (owner Q187, 2026-10-16).

New file rather than test_mail_digest.py: that file's output is pinned by an FS3
completion record (tests/AGENTS.md). Each case fails when the behaviour it names is
reverted: the shared day binding, reply drafts and event cards replying under their mail
card in that thread, results that leave the thread open, and the thread's bell/done mark.
All ids are synthetic.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))

import calendar_confirm  # noqa: E402
import calendar_digest  # noqa: E402
import digest_reaction_actions  # noqa: E402
import triage_approval_gate  # noqa: E402
import triage_binding  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_digest  # noqa: E402
import triage_digest_thread  # noqa: E402
import triage_transport  # noqa: E402

from automation.interop import digest_thread  # noqa: E402

THREAD = "1529000000000000010"
CARD = "1529000000000000020"
_KST = datetime(2026, 10, 17, 8, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def _thread(day: str = "2026-10-17", channel: str = THREAD) -> digest_thread.DigestThread:
    return digest_thread.DigestThread(
        day=day, channel_id=channel, surface="agent-chat-thread", policy_version=8,
        guild_id="1529000000000000001", name=f"📬 기관메일 · 다이제스트 {day}",
    )


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(digest_thread.STATE_ENV, str(tmp_path / "digest-threads"))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    return tmp_path


def _stored(thread: digest_thread.DigestThread) -> None:
    digest_thread.open_thread(thread.day, lambda: thread)


def test_the_day_thread_is_opened_once_and_reused() -> None:
    opened: list[str] = []

    def opener() -> digest_thread.DigestThread:
        opened.append("x")
        return _thread()

    first = digest_thread.open_thread("2026-10-17", opener)
    second = digest_thread.open_thread("2026-10-17", opener)

    assert opened == ["x"]
    assert first == second == digest_thread.load("2026-10-17")
    assert digest_thread.find(THREAD) == first


def test_a_draft_reaction_puts_the_reply_card_in_the_digest_thread_under_its_mail_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given today's digest thread and a mail card in it.
    _stored(_thread())
    calls: list[argparse.Namespace] = []
    monkeypatch.setattr(triage_cli, "cmd_draft", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(digest_reaction_actions, "_pending_draft", lambda _uid: None)
    monkeypatch.setattr(triage_confirm, "post_in", lambda *_a: "m")

    # When ✍️ asks for that mail's reply draft.
    digest_reaction_actions._write_draft({"uid": "u-1", "channel_id": THREAD, "message_id": CARD})

    # Then the draft carries the card as its origin ...
    assert calls[0].digest_card == {"channel_id": THREAD, "message_id": CARD}
    draft = {"id": "d-1", "kind": "reply", "uid": "u-1",
             "origin_channel_id": THREAD, "origin_message_id": CARD}

    # ... its approval binding is the digest thread, not a new request thread ...
    surface = SimpleNamespace(
        reuse_request_thread=lambda *_a: None,
        resolve_new_binding=lambda *_a, **_k: pytest.fail("opened a thread of its own"),
    )
    monkeypatch.setattr(digest_thread, "binding", lambda _s, _k, thread, _d, _o: ("bound", thread.channel_id))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "100000000000000001")
    assert triage_binding._request_binding(surface, "mail-reply", object(), draft, ()) == ("bound", THREAD)

    # ... and the card is posted there as a reply to the mail card.
    posted: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(triage_confirm, "_api", lambda m, p, payload=None: posted.append((m, p, payload)) or {"id": "c-9"})
    monkeypatch.setattr(triage_confirm, "add_reaction", lambda *_a: None)
    gate = triage_approval_gate.MailApprovalGate({**draft, "approval_format": None}, "", "card body")
    gate.post(SimpleNamespace(channel_id=THREAD, key="mail:reply:u-1"))
    assert posted[0][1] == f"/channels/{THREAD}/messages"
    assert posted[0][2]["message_reference"]["message_id"] == CARD


def test_a_draft_asked_outside_the_digest_keeps_its_own_thread() -> None:
    _stored(_thread())
    draft = {"id": "d-2", "kind": "reply", "uid": "u-2", "origin_channel_id": "1529000000000000099"}

    assert triage_digest_thread.digest_binding(SimpleNamespace(), "mail-reply", object(), draft) is None
    assert triage_digest_thread.card_for(draft, "1529000000000000099") == ""


def test_a_digest_draft_result_replies_under_its_card_and_leaves_the_thread_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stored(_thread())
    seen: dict = {}

    def deliver(**kwargs: object) -> str:
        seen.update(kwargs)
        kwargs["transport_factory"](THREAD).send("발송 완료")
        return "r"

    replies: list[tuple[str, str]] = []
    monkeypatch.setattr(triage_confirm, "_origin_notice",
                        lambda: SimpleNamespace(deliver=deliver, ACCEPTS_OWNER_MESSAGE=False,
                                                ThreadOutcome={"DONE": "done"}))
    monkeypatch.setattr(triage_confirm, "post_approval_request",
                        lambda body, channel, reply_to="": replies.append((channel, reply_to)) or "x")
    draft = {"id": "d-1", "subject": "합성", "approval_thread_id": THREAD,
             "origin_channel_id": THREAD, "message_id": "c-9"}

    triage_confirm.notify_result(draft, "발송 완료", outcome=triage_confirm.SENT_OUTCOME)

    assert seen["outcome"] is None
    assert replies == [(THREAD, "c-9")]


def test_a_calendar_card_replies_under_its_mail_card_only_in_that_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, str, dict | None]] = []
    monkeypatch.setattr(calendar_confirm, "_api",
                        lambda m, p, payload=None: sent.append((m, p, payload)) or {"id": "e-1"})
    draft = {"approval_content": "card", "digest_reply_to": CARD, "digest_reply_channel": THREAD}

    calendar_confirm.post_confirmation_message(draft, THREAD)
    calendar_confirm.post_confirmation_message(draft, "1529000000000000099")

    posts = [payload for method, _path, payload in sent if method == "POST"]
    assert posts[0]["message_reference"]["message_id"] == CARD
    assert "message_reference" not in posts[1]


def test_calendar_uses_the_mail_digest_thread_of_the_day(monkeypatch: pytest.MonkeyPatch) -> None:
    _stored(_thread())
    monkeypatch.setattr(digest_thread, "binding", lambda _s, _k, thread, _d, _o: ("bound", thread.channel_id))
    monkeypatch.setattr(calendar_digest.calendar_binding, "approval_directory", object)
    monkeypatch.setattr(calendar_digest.calendar_confirm, "owner_id", lambda: "100000000000000001")

    monkeypatch.setattr(calendar_digest.calendar_gate, "_gate_dir",
                        lambda: pytest.fail("used the calendar's own day thread"))

    assert calendar_digest.daily_binding({"digest_day": "2026-10-17"}) == ("bound", THREAD)
    assert calendar_digest._mail_digest_binding("2026-10-18") is None


def test_the_digest_posts_its_cards_in_the_thread_then_hands_events_over_under_each_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = {"item_no": 1, "uid": "u-1", "subject": "합성 회의", "sender_masked": "h", "category": "important",
            "flags": ("schedule_needed",), "summary": "요약", "recv_date": "2026-10-16T23:00:00Z", "note": "",
            "schedule_text": "10/19 13:30 회의"}
    posts: list[tuple[str, str]] = []
    monkeypatch.setattr(triage_digest, "_open_thread", lambda _now, _count: THREAD)
    monkeypatch.setattr(triage_confirm, "post_in",
                        lambda channel, body: posts.append((channel, body)) or f"p-{len(posts)}")
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda _body: pytest.fail("posted outside the thread"))
    handed: list[tuple[str, str]] = []

    def delegate(_text: str, _uid: str, _day: str, *, reply_to: str, reply_channel: str):
        handed.append((reply_to, reply_channel))
        return triage_transport.ScheduleNote("calendar:" + "a" * 64, card_url="https://discord.com/channels/1/2/3")

    monkeypatch.setattr(triage_transport, "_delegate_schedule", delegate)
    edits: list[str] = []
    monkeypatch.setattr(triage_confirm, "edit_message", lambda _c, _m, content: edits.append(content))

    ids, thread_id = triage_digest._deliver(
        triage_digest.render_digest_parts([item], kst_now=_KST), [item], _KST)
    triage_digest._delegate_after_post(item, {}, ids[1], thread_id, kst_now=_KST)

    assert [channel for channel, _ in posts] == [THREAD, THREAD]
    assert "✅" not in posts[0][1]
    assert handed == [("p-2", THREAD)]
    assert "[승인 카드]" in edits[0]


def test_the_thread_mark_follows_its_undecided_cards() -> None:
    for day, channel in (("2026-10-15", "1529000000000000031"), ("2026-10-16", "1529000000000000032"),
                         ("2026-10-17", "1529000000000000033")):
        _stored(_thread(day, channel))
    calls: list[tuple[str, str, dict]] = []

    def api(method: str, path: str, payload: dict | None = None) -> object:
        calls.append((method, path, payload or {}))
        return {"name": "📬 기관메일 · 다이제스트 2026-10-15"} if method == "GET" else {}

    counts = {"1529000000000000031": 0, "1529000000000000032": 2, "1529000000000000033": 0}
    digest_thread.settle(counts, api)

    patches = {path: payload for method, path, payload in calls if method == "PATCH"}
    assert patches["/channels/1529000000000000031"]["archived"] is True  # done, a newer digest exists
    assert patches["/channels/1529000000000000031"]["name"].startswith("✅ 완료 · ")
    assert patches["/channels/1529000000000000032"]["name"].startswith(digest_thread.PENDING_MARK)
    assert "/channels/1529000000000000033" not in patches  # latest with nothing waiting: unchanged

    calls.clear()
    digest_thread.settle(counts, api)
    assert calls == []  # marks are stored; no rename until the count changes


def test_unknown_calendar_cards_never_close_a_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    _stored(_thread("2026-10-16", "1529000000000000041"))
    _stored(_thread("2026-10-17", "1529000000000000042"))
    monkeypatch.setattr(triage_transport, "_calendar_digest_pending", lambda _ids: None)
    monkeypatch.setattr(triage_confirm, "_api", lambda *_a, **_k: pytest.fail("marked on a guess"))

    triage_digest_thread.settle()


def test_calendar_counts_its_undecided_cards_per_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [SimpleNamespace(channel_id=THREAD), SimpleNamespace(channel_id=THREAD),
               SimpleNamespace(channel_id="1529000000000000099")]
    monkeypatch.setattr("calendar_pending.PendingConfirmStore.load", lambda _self: entries)

    assert calendar_digest.pending_by_thread([THREAD, "1529000000000000050"]) == {
        THREAD: 2, "1529000000000000050": 0,
    }


def test_the_stored_binding_file_is_private(_state: Path) -> None:
    _stored(_thread())
    path = _state / "digest-threads" / "2026-10-17.json"

    assert json.loads(path.read_text(encoding="utf-8"))["channel_id"] == THREAD
    assert path.stat().st_mode & 0o077 == 0

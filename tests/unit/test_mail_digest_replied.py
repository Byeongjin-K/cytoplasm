"""Digest drops "회신 필요" when the owner already replied (owner 2026-10-15).

New file rather than test_mail_digest.py: that file's output is pinned by an
FS3 completion record (tests/AGENTS.md).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))

import triage_core  # noqa: E402
import triage_digest  # noqa: E402
import triage_llm  # noqa: E402
import triage_mode  # noqa: E402
import triage_recipient  # noqa: E402

_OWNER = "owner@inst.example"
_PARTNER = '"상대" <partner@corp.example>'
_SUBJECT = "[기관] 계약서 초안 검토 요청"


def _body(to: str, cc: str = "") -> str:
    return f'---\nto: "{to}"\ncc: "{cc}"\n---\n\nSynthetic body'


def _inbox(uid: str, subject: str, sender: str, date: str) -> dict:
    return {"uid": uid, "folder": "inbox", "subject": subject, "sender": sender, "date": date}


def _sent(uid: str, subject: str, date: str) -> dict:
    return {"uid": uid, "folder": "sent", "subject": subject, "sender": _OWNER, "date": date}


@pytest.mark.parametrize(
    ("inbox_subject", "sent_subject"),
    [
        ("[RE]Re: " + _SUBJECT, "Re: " + _SUBJECT),
        ("RE: [RE]RE: FW: " + _SUBJECT, _SUBJECT),
        ("회신: " + _SUBJECT, "Re[2]: " + _SUBJECT),
    ],
)
def test_reply_candidates_match_same_thread_after_receipt(inbox_subject: str, sent_subject: str) -> None:
    sent = [_sent("s-before", sent_subject, "2026-10-14T02:25:43"),
            _sent("s-after", sent_subject, "2026-10-14T08:06:14")]
    hits = triage_recipient.reply_candidates(inbox_subject, "2026-10-14T05:02:01", sent)
    assert [mail["uid"] for mail in hits] == ["s-after"]


def test_reply_candidates_ignore_other_threads_and_bad_dates() -> None:
    sent = [_sent("s-other", "Re: 3분기 수지분석 협조요청", "2026-10-14T08:06:14")]
    assert triage_recipient.reply_candidates(_SUBJECT, "2026-10-14T05:02:01", sent) == []
    assert triage_recipient.reply_candidates(_SUBJECT, "not-a-date", [_sent("s", _SUBJECT, "2026-10-15T00:00:00")]) == []


def test_reply_candidates_skip_forwards_and_earlier_answers_of_a_recurring_subject() -> None:
    sent = [_sent("s-forward", "FW: " + _SUBJECT, "2026-10-14T08:00:00"),
            _sent("s-last-week", "Re: " + _SUBJECT, "2026-10-07T08:00:00")]
    assert triage_recipient.reply_candidates("Re: " + _SUBJECT, "2026-10-14T05:02:01", sent) == []


def test_addressed_to_checks_to_and_cc_by_address() -> None:
    assert triage_recipient.addressed_to(_body("partner@corp.example"), _PARTNER)
    assert triage_recipient.addressed_to(_body("x@corp.example", "partner@corp.example"), _PARTNER)
    assert not triage_recipient.addressed_to(_body("x@corp.example"), _PARTNER)


def _patch_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: list[dict], sent: list[dict],
               bodies: dict[str, str]) -> None:
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setenv("MAILON_ID", _OWNER)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    transport = triage_digest.triage_transport
    monkeypatch.setattr(transport, "_list_mails", lambda _limit, _sync: inbox)
    monkeypatch.setattr(transport, "_sent_mails", lambda _limit: sent)
    monkeypatch.setattr(transport, "_get_mail", lambda uid: {"uid": uid, "body": bodies[uid]})
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")

    def classify(**kwargs: object) -> tuple[triage_core.Classification, str]:
        return triage_core.Classification(
            category="important", reply_needed=True, schedule_needed=False, budget=False,
            schedule_text="", reason="synthetic",
        ), "stub"

    monkeypatch.setattr(triage_llm, "classify", classify)


def test_run_digest_marks_replied_mail_and_keeps_unanswered_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: two reply-needed mails; the owner answered only the first, after it arrived.
    inbox = [
        _inbox("u-answered", "[RE]Re: " + _SUBJECT, _PARTNER, "2026-10-14T05:02:01"),
        _inbox("u-open", "FW: 수지분석 협조요청", '"동료" <peer@inst.example>', "2026-10-14T04:23:18"),
    ]
    sent = [_sent("s-reply", "Re: " + _SUBJECT, "2026-10-14T08:06:14")]
    bodies = {"u-answered": _body(_OWNER), "u-open": _body(_OWNER),
              "s-reply": _body("partner@corp.example", "peer@inst.example")}
    _patch_run(monkeypatch, tmp_path, inbox, sent, bodies)

    # When: the digest is previewed.
    assert triage_digest.run_digest(limit=10, sync=False, dry_run=True) == 0
    out = capsys.readouterr().out

    # Then: the answered mail says when it was answered; the other still needs a reply.
    cards = out.split("### ")
    answered = next(card for card in cards if "계약서" in card)
    still_open = next(card for card in cards if "수지분석" in card)
    assert "📤 회신함 · 10-14 17:06" in answered and "회신 필요" not in answered
    assert "↩️ 회신 필요" in still_open and "회신함" not in still_open


def test_run_digest_keeps_reply_needed_when_sent_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    inbox = [_inbox("u-answered", _SUBJECT, _PARTNER, "2026-10-14T05:02:01")]
    _patch_run(monkeypatch, tmp_path, inbox, [], {"u-answered": _body(_OWNER)})

    def broken(_limit: int) -> list[dict]:
        raise OSError("db unreadable")

    monkeypatch.setattr(triage_digest.triage_transport, "_sent_mails", broken)
    assert triage_digest.run_digest(limit=10, sync=False, dry_run=True) == 0
    captured = capsys.readouterr()
    assert "↩️ 회신 필요" in captured.out
    assert "DIGEST-SENT-LOOKUP-FAIL OSError" in captured.err


def test_render_replied_badge_without_time_falls_back() -> None:
    item = {"item_no": 1, "subject": "s", "category": "important", "flags": ("replied",),
            "summary": "x", "note": "", "recv_date": "", "replied_at": ""}
    text = triage_digest.render_digest_dm(
        [item], kst_now=datetime(2026, 10, 15, 8, 0, tzinfo=ZoneInfo("Asia/Seoul")))
    assert "🔴 중요 · 📤 회신함" in text.splitlines()


def test_owner_replied_at_skips_unreadable_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_mail(uid: str) -> dict:
        raise triage_digest.triage_gate.GateError("gone", 4)

    monkeypatch.setattr(triage_digest.triage_transport, "_get_mail", get_mail)
    mail = _inbox("u", _SUBJECT, _PARTNER, "2026-10-14T05:02:01")
    assert triage_digest._owner_replied_at(mail, [_sent("s", "Re: " + _SUBJECT, "2026-10-14T08:06:14")]) == ""

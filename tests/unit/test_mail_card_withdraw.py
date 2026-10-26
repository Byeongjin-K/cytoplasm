"""`triage_cli withdraw`: the requester takes back its own still-pending mail card (2026-10-26).

Owner Q367 "유돌이 자기 카드를 취소만 할 수 있게": withdrawing only ever makes a pending card terminal.
It never sends, never approves, and leaves a card the owner already decided to the watcher.
"""
from __future__ import annotations

import argparse

import pytest

from automation.interop import origin_notice
from tests.unit import test_mail_single_live_request as mail
from tests.unit.test_mail_card_v4_supersede import THREAD, _compose, _post
from tests.unit.test_mail_single_live_request import mail_env as mail_env

REASON = "시험 메일이라 필요 없어짐"


@pytest.fixture
def card(mail_env, monkeypatch: pytest.MonkeyPatch):
    fake = mail_env[0]
    fake.threads[THREAD] = "점심 메일"
    writes: list[tuple[str, str, dict | None]] = []
    original = fake.api

    def api(method, path, payload=None):
        if method in {"POST", "PATCH"}:
            writes.append((method, path, payload))
        if method == "PATCH":
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                fake.contents[parts[3]] = str((payload or {}).get("content", ""))
            return {"id": parts[1]}
        return original(method, path, payload)

    monkeypatch.setattr(mail.triage_confirm, "_api", api)
    monkeypatch.setattr(mail.triage_confirm, "_origin_notice", lambda: origin_notice)
    monkeypatch.setattr(mail.triage_confirm, "_dm_transport",
                        lambda channel: mail.triage_confirm._CardReply(channel, ""))

    def no_send(*_args, **_kwargs):
        raise AssertionError("withdraw must never send mail")

    monkeypatch.setattr(mail.triage_gate, "_run_send", no_send)
    stored = _post(_compose("시험 본문"))
    writes.clear()
    return fake, stored, writes, mail_env[2]


def _withdraw(draft_id: str) -> int:
    return mail.triage_cli.cmd_withdraw(
        argparse.Namespace(draft=draft_id, reason=REASON, by="유돌"))


def test_own_pending_card_is_withdrawn_marked_and_never_sent(card) -> None:
    fake, stored, writes, drafts = card
    # When: the requester withdraws its pending card.
    assert _withdraw(stored["id"]) == 0
    # Then: the record is terminal with who and why, and no pending draft holds the card.
    record = mail._stored(drafts, stored["id"])
    assert record["status"] == "expired"
    assert record["expired_reason"] == "withdrawn-by-requester"
    assert record["withdrawn_by"] == "유돌" and record["withdraw_reason"] == REASON
    assert record["withdrawn_at"].endswith("Z")
    assert record["sha256"] == stored["sha256"]
    pending = [str(r["id"]) for _, r, _ in mail.triage_approval._pending_drafts()]
    assert stored["id"] not in pending
    # Then: the card itself shows the cancel mark with who withdrew it.
    content = fake.contents[stored["message_id"]]
    assert content.startswith("⛔") and "유돌" in content and REASON in content
    # Then: the thread gets the ⛔ notice and keeps its name (no thread PATCH).
    notices = [p for m, path, p in writes if m == "POST" and path == f"/channels/{THREAD}/messages"]
    assert len(notices) == 1 and "⛔ 발송 취소" in str(notices[0]["content"])
    assert not [path for m, path, _ in writes if m == "PATCH" and path == f"/channels/{THREAD}"]
    # When: the owner presses ✅ on the withdrawn card later; Then: nothing can load it to send.
    fake.approved.add(stored["message_id"])
    with pytest.raises(mail.triage_gate.GateError):
        mail.triage_gate.load_draft(stored["id"])


def test_a_card_the_owner_approved_is_refused_and_left_pending(card) -> None:
    fake, stored, writes, drafts = card
    fake.approved.add(stored["message_id"])
    before = fake.contents[stored["message_id"]]
    with pytest.raises(mail.triage_gate.GateError) as refused:
        _withdraw(stored["id"])
    assert refused.value.exit_code == 1
    assert mail._stored(drafts, stored["id"])["status"] == "pending"
    assert fake.contents[stored["message_id"]] == before and writes == []


@pytest.mark.parametrize("status", ["executed", "blocked", "expired"])
def test_a_sent_or_finished_card_is_refused(card, status: str) -> None:
    fake, stored, writes, drafts = card
    path = drafts / f"{stored['id']}.json"
    mail.triage_gate.write_json(path, {**mail._stored(drafts, stored["id"]), "status": status})
    with pytest.raises(mail.triage_gate.GateError) as refused:
        _withdraw(stored["id"])
    assert refused.value.exit_code == 1
    assert mail._stored(drafts, stored["id"])["status"] == status and writes == []


def test_a_card_being_sent_right_now_is_refused(card) -> None:
    _fake, stored, writes, drafts = card
    lease = mail.triage_approval.confirm_lease()
    with lease.hold(mail.triage_approval.approval_key(stored)) as owned:
        assert owned
        with pytest.raises(mail.triage_gate.GateError) as refused:
            _withdraw(stored["id"])
    assert refused.value.exit_code == 1
    assert mail._stored(drafts, stored["id"])["status"] == "pending" and writes == []


@pytest.mark.parametrize("draft_id", ["abcdef", "../x", "28fe93;id"])
def test_unknown_or_malformed_ids_are_refused(card, draft_id: str) -> None:
    _fake, _stored, writes, _drafts = card
    with pytest.raises(mail.triage_gate.GateError) as refused:
        _withdraw(draft_id)
    assert refused.value.exit_code == 3 and writes == []


def test_the_cli_parses_withdraw_with_its_three_values() -> None:
    args = mail.triage_cli.build_parser().parse_args(
        ["withdraw", "--draft", "28fe93", "--reason", REASON, "--by", "유돌"])
    assert (args.func, args.draft, args.reason, args.by) == (
        mail.triage_cli.cmd_withdraw, "28fe93", REASON, "유돌")

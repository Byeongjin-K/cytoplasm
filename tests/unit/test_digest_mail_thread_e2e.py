"""Each digest mail's 📄/✍️ runs in that mail's own thread (owner 2026-11-16).

Owner: "다이제스트에서 전문 확인 및 초안 작성 누르면 별도 스레드로 진행되게 해". The simulated Discord
of ``test_digest_one_thread_e2e`` stands in for the network; the digest, the reaction path,
the draft gate and the watch tick are the shipped code. ``screen()`` prints what the owner
sees. All ids, names and addresses are synthetic.
"""
from __future__ import annotations

import argparse
import sqlite3
import threading
from datetime import UTC, datetime
from email.message import Message
from urllib.error import HTTPError

import pytest

from tests.unit.test_digest_one_thread_e2e import (
    AGENT_CHAT, DAY1, World, _calendar_in_process, _digest, _mails, world,
)

import digest_reaction_actions  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_gate  # noqa: E402
import triage_store  # noqa: E402

__all__ = ["_calendar_in_process", "world"]
_TICK = datetime(2026, 10, 16, 23, 5, tzinfo=UTC)


def screen(sim: World) -> str:
    lines = []
    for thread_id in sim.threads_created:
        thread = sim.channels[thread_id]
        state = " (archived)" if thread.get("archived") else ""
        lines.append(f"thread «{thread['name']}»{state}")
        for item in sim.in_channel(thread_id):
            head = item["content"].splitlines()[0] if item["content"] else "(empty)"
            under = f" ↳ reply to {item['reply_to'][-4:]}" if item["reply_to"] else ""
            lines.append(f"   {item['id'][-4:]}{under}: {head[:90]}  {''.join(item['reactions'])}".rstrip())
    return "\n".join(lines)


def _press(sim: World, card: dict, emoji: str, action: str) -> int:
    sim.react(card["id"], emoji)
    return digest_reaction_actions.run_card(card["id"], now=_TICK, pressed=action)


def _again(sim: World, card: dict, emoji: str, action: str) -> int:
    """The owner takes his reaction off and presses it once more (a new press event)."""
    sim.messages[card["id"]]["reactions"][emoji].remove("1529100000000000001")
    return _press(sim, card, emoji, action)


def test_each_mail_runs_in_its_own_thread(world: World, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    _digest(monkeypatch, DAY1, _mails(("u-1", "합성 의견 요청", False), ("u-2", "합성 예산 문의", False)), {})
    digest = world.threads_created[0]
    card_a, card_b = [m for m in world.in_channel(digest) if m["content"].startswith("### ")]

    # S1 happy: 📄 on mail A opens A's own thread with its full text; the card gets one link line.
    assert _press(world, card_a, "📄", "body") == 1
    assert len(world.threads_created) == 2
    thread_a = world.channels[world.threads_created[1]]
    assert thread_a["parent_id"] == AGENT_CHAT and thread_a["name"] == "🔄 ✉️ 합성 의견 요청"
    assert any(m["content"].startswith("📄 **전문** · 합성 의견 요청") for m in world.in_channel(thread_a["id"]))
    links = [m for m in world.in_channel(digest) if m["reply_to"] == card_a["id"]]
    assert len(links) == 1 and thread_a["id"] in links[0]["content"]

    # S1 happy: ✍️ on mail A -> no new thread; the note, preview and approval card are in A's thread.
    assert _press(world, card_a, "✍️", "draft") == 1
    assert len(world.threads_created) == 2
    in_a = [m["content"] for m in world.in_channel(thread_a["id"])]
    assert in_a[2] == digest_reaction_actions.DRAFT_ACK
    assert any(text.startswith("📨 **수신메일 회신 발송 승인 요청**") for text in in_a)
    assert any(text.startswith("✍️ **회신 초안을 썼습니다**") for text in in_a)
    draft_a = next(d for d in triage_gate.list_drafts() if d["uid"] == "u-1")
    assert draft_a["channel_id"] == draft_a["approval_thread_id"] == thread_a["id"]
    assert thread_a["name"] == "🔔 ✉️ 합성 의견 요청"
    assert len([m for m in world.in_channel(digest) if m["reply_to"] == card_a["id"]]) == 1

    # S2 same mail twice: each repeat press points at the same thread and posts nothing else.
    before = len(world.in_channel(thread_a["id"]))
    assert _again(world, card_a, "📄", "body") == 0
    assert _again(world, card_a, "✍️", "draft") == 0
    assert len(world.threads_created) == 2
    assert len(world.in_channel(thread_a["id"])) == before
    assert len([m for m in world.in_channel(digest) if m["reply_to"] == card_a["id"]]) == 3

    # S3 two mails + S4 rename 429: 📄 on mail B opens a second thread; B's thread refuses its
    # 🔔 rename with 429 and ✍️ still posts and binds B's card there, the mark waiting for a tick.
    def limited(method: str, path: str, payload: dict | None = None) -> object:
        if method == "PATCH" and "name" in (payload or {}) and path == f"/channels/{world.threads_created[-1]}":
            raise HTTPError("https://discord.invalid", 429, "name edits limited", Message(), None)
        return world(method, path, payload)

    assert _press(world, card_b, "📄", "body") == 1
    assert len(world.threads_created) == 3
    thread_b = world.channels[world.threads_created[2]]
    monkeypatch.setattr(triage_confirm, "_api", limited)
    assert _press(world, card_b, "✍️", "draft") == 1
    draft_b = next(d for d in triage_gate.list_drafts() if d["uid"] == "u-2")
    assert draft_b["approval_thread_id"] == thread_b["id"] and draft_b["message_id"]
    assert any(m["id"] == draft_b["message_id"] for m in world.in_channel(thread_b["id"]))
    assert thread_b["name"] == "🔄 ✉️ 합성 예산 문의"
    text_a = "\n".join(m["content"] for m in world.in_channel(thread_a["id"]))
    text_b = "\n".join(m["content"] for m in world.in_channel(thread_b["id"]))
    assert "합성 예산 문의" not in text_a and "합성 의견 요청" not in text_b
    monkeypatch.setattr(triage_confirm, "_api", world)
    triage_cli.cmd_watch(argparse.Namespace())
    assert thread_b["name"] == "🔔 ✉️ 합성 예산 문의"
    assert len(world.threads_created) == 3

    with capsys.disabled():
        print("\n" + screen(world))


def _cards(sim: World) -> tuple[str, list[dict]]:
    _digest_thread = sim.threads_created[0]
    return _digest_thread, [m for m in sim.in_channel(_digest_thread) if m["content"].startswith("### ")]


def test_a_store_failure_after_discord_opened_the_thread_reuses_it(
        world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _digest(monkeypatch, DAY1, _mails(("u-1", "합성 의견 요청", False)), {})
    _, (card,) = _cards(world)
    real = triage_store.save_digest_mail_thread
    failures = [sqlite3.OperationalError("database is locked")]

    def flaky(*args: object, **kwargs: object) -> None:
        if failures:
            raise failures.pop()
        real(*args, **kwargs)

    monkeypatch.setattr(triage_store, "save_digest_mail_thread", flaky)
    world.react(card["id"], "📄")
    assert digest_reaction_actions.run_card(card["id"], now=_TICK, pressed="body") == 0
    assert len(world.threads_created) == 2  # Discord opened the mail thread; the store write failed
    assert digest_reaction_actions.run_card(card["id"], now=_TICK, pressed="body") == 1
    assert len(world.threads_created) == 2
    thread = world.threads_created[1]
    assert triage_store.digest_mail_thread(triage_gate.db_path(), "u-1")["thread_id"] == thread
    assert any(m["content"].startswith("📄 **전문**") for m in world.in_channel(thread))


def _reply_under_card(digest: str, card: dict, uid: str) -> dict:
    """The owner's ordinary reply under the digest card, before the mail had its own thread."""
    triage_cli.cmd_draft(argparse.Namespace(
        uid=uid, instruction="합성 지시: 감사 인사로 답해 줘", attachment=[], no_post=False,
        with_evidence=False, reply_all=False,
        digest_card={"channel_id": digest, "message_id": card["id"]},
    ))
    draft = next(d for d in triage_gate.list_drafts() if d["uid"] == uid and d["status"] == "pending")
    assert draft["approval_thread_id"] == digest
    return draft


def test_an_earlier_undecided_card_moves_into_the_mail_thread(
        world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _digest(monkeypatch, DAY1, _mails(("u-1", "합성 의견 요청", False)), {})
    digest, (card,) = _cards(world)
    old = _reply_under_card(digest, card, "u-1")
    assert _press(world, card, "📄", "body") == 1
    assert _press(world, card, "✍️", "draft") == 1
    thread = world.threads_created[1]
    pending = [d for d in triage_gate.list_drafts() if d["uid"] == "u-1" and d["status"] == "pending"]
    assert len(pending) == 1 and pending[0]["id"] != old["id"]
    assert pending[0]["body"] == old["body"] and pending[0]["approval_thread_id"] == thread
    assert any(m["id"] == pending[0]["message_id"] for m in world.in_channel(thread))
    assert world.messages[old["message_id"]]["content"] == digest_reaction_actions.MOVED_NOTICE
    closed = next(d for d in triage_gate.list_drafts() if d["id"] == old["id"])
    assert closed["status"] == "expired"


def test_a_card_the_owner_already_pressed_stays_where_it_is(
        world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _digest(monkeypatch, DAY1, _mails(("u-1", "합성 의견 요청", False)), {})
    digest, (card,) = _cards(world)
    old = _reply_under_card(digest, card, "u-1")
    world.react(old["message_id"], "✅")
    assert _press(world, card, "✍️", "draft") == 1
    drafts = [d for d in triage_gate.list_drafts() if d["uid"] == "u-1"]
    assert [d["id"] for d in drafts] == [old["id"]] and drafts[0]["status"] == "pending"
    thread = world.threads_created[1]
    assert not any(m["content"].startswith("📨") for m in world.in_channel(thread))
    assert world.messages[old["message_id"]]["content"] != digest_reaction_actions.MOVED_NOTICE


def test_body_and_draft_at_once_post_one_link_line(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _digest(monkeypatch, DAY1, _mails(("u-1", "합성 의견 요청", False)), {})
    digest, (card,) = _cards(world)
    real = triage_store.digest_card_link
    both_read = threading.Barrier(2, timeout=2)

    def read_then_meet(*args: object) -> str | None:
        found = real(*args)
        try:  # without the lock both callers read "no link" here and both post one
            both_read.wait()
        except threading.BrokenBarrierError:
            pass  # the other caller is held behind the lock, as it should be
        return found

    monkeypatch.setattr(triage_store, "digest_card_link", read_then_meet)
    row = {"uid": "u-1", "channel_id": digest, "message_id": card["id"], "subject": "합성 의견 요청"}
    workers = [threading.Thread(target=digest_reaction_actions._mail_thread, args=(row, "합성 의견 요청"))
               for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
    assert len(world.threads_created) == 2
    assert len([m for m in world.in_channel(digest) if m["reply_to"] == card["id"]]) == 1

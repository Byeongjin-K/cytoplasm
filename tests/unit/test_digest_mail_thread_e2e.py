"""Each digest mail's 📄/✍️ runs in that mail's own thread (owner 2026-11-16).

Owner: "다이제스트에서 전문 확인 및 초안 작성 누르면 별도 스레드로 진행되게 해". The simulated Discord
of ``test_digest_one_thread_e2e`` stands in for the network; the digest, the reaction path,
the draft gate and the watch tick are the shipped code. ``screen()`` prints what the owner
sees. All ids, names and addresses are synthetic.
"""
from __future__ import annotations

import argparse
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

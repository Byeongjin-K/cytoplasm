"""The digest's calendar line links to the approval card instead of showing a bare hash.

Owner 2026-10-16: the line ``🗓️ 일정 초안 `calendar:<hash>``` did not say where to approve.
Since 2026-10-16 (owner Q187) the mail card is posted first and the calendar card replies
under it, so the line with the link is written by editing the posted mail card.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.unit.test_calendar_digest_daily_thread import TEXTS, digest, pending
from tests.unit.test_calendar_single_live_request import (
    AGENT_CHAT_GUILD_ID, REQUEST_THREAD_ID, calendar_confirm, calendar_gate,
)
from tests.unit.test_calendar_digest_daily_thread import calendar_env as calendar_env
from tests.unit.test_calendar_digest_daily_thread import flow as flow
from tests.unit.test_calendar_digest_daily_thread import triage_digest

_KST_NOW = datetime(2026, 10, 16, 8, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def _item(**fields: object) -> dict:
    return {
        "item_no": 1, "uid": "uid-1", "subject": "Synthetic", "sender_masked": "x",
        "category": "important", "flags": ("schedule_needed",), "summary": "s",
        "recv_date": "2026-10-16T00:00:00Z", **fields,
    }


def test_render_when_the_card_link_is_known_then_the_line_links_it_with_the_event() -> None:
    card = triage_digest.render_item(_item(
        note="calendar:" + "a" * 64, calendar_card="https://discord.com/channels/11/22/33",
        calendar_when="10/19(월) 13:30–14:30", calendar_title="Synthetic review",
    ), kst_now=_KST_NOW)

    assert card.splitlines()[-1] == (
        "🗓️ 일정 초안 → [승인 카드](<https://discord.com/channels/11/22/33>) · 10/19(월) 13:30–14:30 Synthetic review"
    )
    assert "a" * 64 not in card


@pytest.mark.parametrize("note", ["calendar-ambiguous", "calendar-unparsed"])
def test_render_when_the_date_is_not_settled_then_the_line_says_so_in_korean(note: str) -> None:
    # Owner 2026-10-17: the English cause was shown for a mail whose date is not set yet.
    card = triage_digest.render_item(_item(note=note), kst_now=_KST_NOW)

    assert card.splitlines()[-1] == "🗓️ 일정 예정 · 날짜 미정 — 날짜가 정해지면 그때 등록"


@pytest.mark.parametrize("note", [
    "calendar-refused", "calendar-misconfigured", "calendar-routing", "calendar-unavailable",
    "calendar-exec-failed", "calendar-timeout", "calendar-spawn-failed", "calendar-failed-rc9",
])
def test_render_when_the_draft_failed_then_the_reason_is_korean_and_the_code_stays_hidden(
    note: str,
) -> None:
    line = triage_digest.render_item(_item(note=note), kst_now=_KST_NOW).splitlines()[-1]

    assert line.startswith("🗓️ 일정 ")
    assert "calendar" not in line
    assert not re.search(r"[A-Za-z]", line)


def _calendar_line(monkeypatch) -> list[str]:
    """The final text of each edited mail card (the flat layout: no digest thread opens)."""
    posts: list[str] = []
    edits: list[str] = []
    monkeypatch.setattr(triage_digest.triage_confirm, "dm_owner",
                        lambda body: posts.append(body) or f"m-{len(posts)}")
    monkeypatch.setattr(triage_digest.triage_confirm, "edit_message",
                        lambda _channel, _message, content: edits.append(content))
    monkeypatch.setattr(triage_digest, "_flat_channel", lambda: "owner-channel")
    monkeypatch.setattr(triage_digest, "_open_thread", lambda _now, _count: "")
    return edits


def test_digest_card_links_the_real_approval_card_message(flow, monkeypatch) -> None:
    # Given the real digest -> calendar CLI -> card path, with snowflake message ids and the
    # day's thread binding already stored with its guild, as the node's day file has.
    posted = calendar_confirm._api

    def snowflake_ids(method, path, payload=None):
        response = posted(method, path, payload)
        if method == "POST" and isinstance(response, dict) and str(response.get("id", "")).startswith("msg-"):
            return {**response, "id": str(1526487936065953000 + int(response["id"][4:]))}
        return response

    monkeypatch.setattr(calendar_confirm, "_api", snowflake_ids)
    day = calendar_gate._gate_dir() / "daily-threads" / "2026-09-18.json"
    day.parent.mkdir(parents=True, exist_ok=True)
    day.write_text(json.dumps({
        "approval_guild_id": AGENT_CHAT_GUILD_ID, "channel_id": REQUEST_THREAD_ID, "kind": "calendar",
        "policy_version": 8, "surface": "agent-chat-thread",
    }), encoding="utf-8")
    posts = _calendar_line(monkeypatch)

    digest(flow, (TEXTS[0],))

    # Then the mail card links that posted approval card by guild, thread and message.
    entry = pending()[0]
    lines = [line for line in posts[-1].splitlines() if line.startswith("🗓️")]
    assert lines == [
        f"🗓️ 일정 초안 → [승인 카드](<https://discord.com/channels/{AGENT_CHAT_GUILD_ID}/"
        f"{entry.channel_id}/{entry.dm_message_id}>) · 10/1(목) 15:00–16:00 alpha meeting"
    ]


def test_digest_card_names_the_thread_when_the_card_id_is_not_a_link(flow, monkeypatch) -> None:
    # Given a card id that cannot form a Discord link.
    posts = _calendar_line(monkeypatch)

    digest(flow, (TEXTS[0],))

    # Then no link is invented: the line names the thread and still shows the event.
    lines = [line for line in posts[-1].splitlines() if line.startswith("🗓️")]
    assert lines == [
        "🗓️ 일정 초안 · 승인 카드는 그날 「캘린더 · 다이제스트」 스레드 · 10/1(목) 15:00–16:00 alpha meeting"
    ]

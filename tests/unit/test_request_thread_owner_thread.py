"""A request card joins the owner's existing request thread instead of opening a second one."""
from __future__ import annotations

import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_surface import ApprovalKind, RequestThread
from tests.unit.test_approval_directory import FakeApi


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": "222"}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    return tmp_path / "private" / "approval-directory.json"


def test_owner_message_inside_a_thread_posts_the_card_there(configured: Path) -> None:
    # Given: the owner wrote the request inside a thread under agent-chat.
    api = FakeApi("identity-a", {("GET", "/channels/777"): {
        "type": 11, "name": "점심 메일", "parent_id": "222", "guild_id": "111",
    }})
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    # When: a card for that message asks for its request thread.
    thread = directory.agent_chat_request_thread(ApprovalKind.MAIL_COMPOSE, RequestThread("점심", "777", "888"))
    # Then: the card joins that thread; no announcement, no new thread.
    assert thread == "777"
    assert [(method, path) for method, path, _ in api.calls] == [("GET", "/channels/777")]


def test_owner_message_with_its_own_thread_reuses_it(configured: Path) -> None:
    # Given: a thread already hangs on the owner's channel message.
    path = "/channels/222/messages/888/threads"
    api = FakeApi("identity-a", {("POST", path): HTTPError("https://discord.test", 400, "exists", Message(), None)})
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    # When: a card for that message asks for its request thread.
    thread = directory.agent_chat_request_thread(ApprovalKind.MAIL_COMPOSE, RequestThread("점심", "222", "888"))
    # Then: thread id == message id, and only the one POST was made.
    assert thread == "888"
    assert [(method, p) for method, p, _ in api.calls] == [("POST", path)]


def test_owner_message_without_a_thread_opens_one_on_that_message(configured: Path) -> None:
    # Given: the owner's channel message has no thread yet.
    api = FakeApi("identity-a", {
        ("POST", "/channels/222/messages/888/threads"): {"id": "888", "guild_id": "111"},
        ("POST", "/channels/888/messages"): {"id": "999"},
    })
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    # When: a card for that message asks for its request thread.
    thread = directory.agent_chat_request_thread(ApprovalKind.MAIL_COMPOSE, RequestThread("점심", "222", "888"))
    # Then: the thread hangs on the owner's message, not on a new announcement.
    assert thread == "888"
    assert ("POST", "/channels/222/messages") not in [(m, p) for m, p, _ in api.calls]


def test_thread_under_another_channel_keeps_the_announcement_path(configured: Path) -> None:
    # Given: the origin is a thread, but not under agent-chat.
    api = FakeApi("identity-a", {
        ("GET", "/channels/777"): {"type": 11, "name": "x", "parent_id": "999", "guild_id": "111"},
        ("POST", "/channels/222/messages"): {"id": "555"},
        ("POST", "/channels/222/messages/555/threads"): {"id": "444", "guild_id": "111"},
    })
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    # When: a card for that message asks for its request thread.
    thread = directory.agent_chat_request_thread(ApprovalKind.MAIL_COMPOSE, RequestThread("점심", "777", "888"))
    # Then: it announces in agent-chat and opens its own thread, as before.
    assert thread == "444"

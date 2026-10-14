"""Offline multi-message Discord and real temporary memory setup for per-line checks."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.reaction_approval import JsonValue
from automation.memory_relocate.choice_accounts import Accounts
from automation.memory_relocate.choice_files import Mailbox
from automation.memory_relocate.cron import memory_relocate_watch as watch
from automation.obsidian_write.config import ObsidianWriteConfig
from memory_choice_fakes import CHANNEL, OWNER, Reactions, MessageSink


class LinesTransport(Reactions):
    """Real message/reaction identity distinguishes every card; all I/O is in memory."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: dict[str, str] = {}
        self.reactors: dict[tuple[str, str], tuple[tuple[str, bool], ...]] = {}
        self.seeds: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.message_reads: list[str] = []

    def post_message(self, channel: str, content: str) -> str:
        assert channel == CHANNEL
        message = str(9100 + len(self.messages))
        self.messages[message] = content
        return message

    def get_message(self, channel: str, message: str) -> str | None:
        assert channel == CHANNEL
        return self.messages.get(message)

    def add_reaction(self, channel: str, message: str, emoji: str) -> None:
        assert channel == CHANNEL
        if message not in self.messages:
            raise HTTPError("https://discord.invalid", 404, "missing", {}, None)
        self.seeds.append((message, emoji))

    def get_reaction_users(self, channel: str, message: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        assert channel == CHANNEL
        self.reads.append(emoji)
        return self.reactors.get((message, emoji), ())

    def delete_message(self, channel: str, message: str) -> None:
        self.deleted.append(message)
        self.messages.pop(message)

    def api(self, method: str, path: str, payload: dict[str, JsonValue] | None = None) -> JsonValue:
        prefix = f"/channels/{CHANNEL}/messages/"
        if method == "GET" and path.startswith(prefix):
            message = path.removeprefix(prefix)
            self.message_reads.append(message)
            if message not in self.messages:
                raise HTTPError("https://discord.invalid", 404, "missing", {}, None)
            emojis = {emoji for identity, emoji in self.seeds if identity == message}
            emojis.update(emoji for identity, emoji in self.reactors if identity == message)
            return {
                "id": message, "channel_id": CHANNEL, "content": self.messages[message],
                "author": {"bot": True}, "edited_timestamp": None,
                "reactions": [
                    {"emoji": {"id": None, "name": emoji},
                     "count": 1 + len(self.reactors.get((message, emoji), ()))}
                    for emoji in sorted(emojis)
                ],
            }
        return super().api(method, path, payload)


@dataclass(frozen=True, slots=True)
class Offline:
    transport: LinesTransport
    accounts: Accounts
    directory: DiscordChannelDirectory
    memory: Path
    state: Path
    notices: list[tuple[str, str]]


def offline_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Offline:
    """Wire the real existing cron to private tmp files and an offline transport."""
    transport = LinesTransport()
    memory = tmp_path / "memories"
    memory.mkdir()
    (memory / "USER.md").write_text("", encoding="utf-8")
    (memory / "MEMORY.md").write_text("", encoding="utf-8")
    roots = (tmp_path / "agent", tmp_path / "peer")
    for root in roots:
        root.mkdir(mode=0o750)
    participants = Accounts(*(Mailbox(root, os.getuid(), os.getgid()) for root in roots))
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": "9000"}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "offline")
    monkeypatch.setattr(watch, "_owner_id", lambda: OWNER)
    state = tmp_path / "state" / "relocations.json"
    monkeypatch.setattr(watch, "STATE_PATH", state)
    monkeypatch.setattr(watch, "MEMORY_DIR", memory)
    module = "automation.memory_relocate.effects_live"
    monkeypatch.setattr(module + ".accounts", lambda: participants)
    monkeypatch.setattr(module + ".DiscordTransport", lambda *args: transport)
    monkeypatch.setattr("automation.interop.reaction_approval.DiscordTransport", lambda *args: transport)
    monkeypatch.setattr(module + ".load_config",
                        lambda: ObsidianWriteConfig("git@example.invalid:vault.git", tmp_path / "clone"))
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr("automation.memory_relocate.choice_notice.MessageTransport",
                        lambda token, channel_id: MessageSink(channel_id, notices))
    return Offline(transport, participants, DiscordChannelDirectory("offline", OWNER, transport.api),
                   memory, state, notices)

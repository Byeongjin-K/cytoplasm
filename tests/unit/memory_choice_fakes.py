"""Offline batch fixtures and a reaction transport with observable reads."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final
from urllib.error import HTTPError

from automation.interop.reaction_approval import DiscordTransport, JsonValue
from automation.memory_curator.binding import entry_digest
from automation.memory_relocate.model import RelocationRecord
from automation.memory_relocate.propose import build_batch, build_proposed_record

NOW: Final = datetime(2026, 10, 14, tzinfo=UTC)
OWNER: Final = "9001"
CHANNEL: Final = "9002"
MESSAGE: Final = "9003"


def members(count: int = 3) -> tuple[tuple[RelocationRecord, str], ...]:
    texts = tuple(f"Synthetic reference {index}." for index in range(count))
    records = tuple(build_proposed_record(
        text, source_kind="user", entry_sha256=entry_digest("user", text),
        reclaimable_chars=len(text), binding_kind="obsidian-write",
        binding_surface="agent-chat-thread", binding_channel_id=CHANNEL,
        binding_policy_version=8, now=NOW, destination="memory",
    ) for text in texts)
    bound = build_batch(tuple(zip(records, texts, strict=True)), fits=lambda batch: True)
    return tuple((replace(record, status="posted", message_id=MESSAGE, approval_thread_id=CHANNEL), text)
                 for record, text in zip(bound, texts, strict=True))


class Reactions(DiscordTransport):
    """Mutable fake of the narrow reaction/card boundary, not of the deletion engine."""

    owner_id: str = OWNER

    def __init__(self, content: str = "") -> None:
        super().__init__("test", OWNER)
        self.content: str | None = content
        self.edited: bool = False
        self.users: dict[str, tuple[tuple[str, bool], ...]] = {}
        self.reads: list[str] = []
        self.seeded: list[str] = []

    def api(self, method: str, path: str, payload: dict[str, JsonValue] | None = None) -> JsonValue:
        if method == "PATCH" and path == f"/channels/{CHANNEL}":
            return None
        if method == "GET" and path == f"/channels/{CHANNEL}":
            return {"type": 11, "parent_id": "9000", "name": "synthetic", "guild_id": "9004"}
        if method == "GET" and path == f"/channels/{CHANNEL}/messages/{MESSAGE}":
            if self.content is None:
                raise HTTPError("https://discord.invalid/message", 404, "missing", {}, None)
            return {"id": MESSAGE, "channel_id": CHANNEL, "content": self.content,
                    "edited_timestamp": "edited" if self.edited else None, "author": {"bot": True}}
        raise AssertionError(f"Unexpected offline API request: {method} {path}")

    def get_message(self, channel: str, message: str) -> str | None:
        return self.content

    def get_reaction_users(self, channel: str, message: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        self.reads.append(emoji)
        return self.users.get(emoji, ())

    def add_reaction(self, channel: str, message: str, emoji: str) -> None:
        self.seeded.append(emoji)

    def post_message(self, channel: str, content: str) -> str:
        self.content = content
        return MESSAGE

    def delete_message(self, channel: str, message: str) -> None:
        self.content = None


@dataclass(frozen=True, slots=True)
class Sent:
    message_id: str


@dataclass(frozen=True, slots=True)
class MessageSink:
    """Offline transport seam; real owner-message rendering and delivery stay in the test."""
    channel_id: str
    deliveries: list[tuple[str, str]]

    def send(self, body: str) -> tuple[Sent, ...]:
        self.deliveries.append((self.channel_id, body))
        return (Sent("9005"),)

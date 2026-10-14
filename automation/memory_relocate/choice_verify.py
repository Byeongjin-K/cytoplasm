"""Independent Discord readback: immutable card, private surface, real owner decision."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_surface import (
    ApprovalBinding, ApprovalKind, ApprovalSurface, validate_stored_binding,
)
from automation.interop.reaction_approval import DiscordTransport

from .choice_request import ChoiceRequest
from .choices import Choice, ChoiceError, owner_reacted, read_choices
from .line_choices import RENDER_VERSION as LINE_VERSION, read_choice


@dataclass(frozen=True, slots=True)
class Verifier:
    transport: DiscordTransport
    directory: DiscordChannelDirectory

    def decision(self, request: ChoiceRequest) -> Literal["approved", "cancelled", "pending"]:
        self.verify_card(request)
        if request.record.render_version == LINE_VERSION:
            if request.record.choice is not None:
                return "approved"  # Frozen choice still requires exact message readback.
            return "approved" if self.choices(request) else "pending"
        record = request.record
        def read(emoji: str) -> tuple[tuple[str, bool], ...]:
            return self.transport.get_reaction_users(record.channel_id, record.message_id or "", emoji)
        if owner_reacted(read("⛔"), self.transport.owner_id):
            return "cancelled"
        return "approved" if owner_reacted(read("✅"), self.transport.owner_id) else "pending"

    def verify_card(self, request: ChoiceRequest) -> None:
        """Message identity, exact displayed bytes and private surface must all match."""
        record = request.record
        _ = validate_stored_binding(
            ApprovalBinding(ApprovalKind.OBSIDIAN_WRITE, ApprovalSurface.AGENT_CHAT_THREAD,
                            record.channel_id, 8),
            self.directory, self.transport.owner_id,
        )
        raw = self.transport.api("GET", f"/channels/{record.channel_id}/messages/{record.message_id}")
        match raw:
            case {"id": str() as message, "channel_id": str() as channel,
                  "content": str() as content, "edited_timestamp": None, "author": {"bot": True}}:
                if (message, channel, content) != (record.message_id, record.channel_id, request.content):
                    raise ChoiceError("card_binding")
            case _:
                raise ChoiceError("card_missing_or_edited")
    def choices(self, request: ChoiceRequest) -> tuple[Choice, ...]:
        record = request.record
        if record.render_version == LINE_VERSION:
            self.verify_card(request)
            def read(emoji: str) -> tuple[tuple[str, bool], ...]:
                return self.transport.get_reaction_users(record.channel_id, record.message_id or "", emoji)
            selected = read_choice(read, self.transport.owner_id)
            self.verify_card(request)
            confirmed = read_choice(read, self.transport.owner_id)
            return () if selected is None or selected != confirmed else (selected,)
        return read_choices(
            lambda emoji: self.transport.get_reaction_users(record.channel_id, record.message_id or "", emoji),
            self.transport.owner_id, len(request.members),
        )

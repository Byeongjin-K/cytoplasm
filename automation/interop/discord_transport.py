"""Sequential Discord REST sender for protocol reports."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from email.message import Message
from typing import Final
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from automation.interop.chunker import chunk_message


DISCORD_API: Final = "https://discord.com/api/v10"


@dataclass(frozen=True, slots=True)
class SentMessage:
    """Discord response metadata sufficient to prove send order."""

    message_id: str


@dataclass(frozen=True, slots=True)
class DiscordTransport:
    """Send ordered chunks and honor Discord's Retry-After rate-limit response."""

    token: str
    channel_id: str

    def send(self, body: str) -> tuple[SentMessage, ...]:
        """Send every chunk serially, preserving Discord creation order."""
        return tuple(self._send_chunk(chunk) for chunk in chunk_message(body))

    def _send_chunk(self, chunk: str) -> SentMessage:
        from automation.notice_bot import resolve_token

        request = Request(
            f"{DISCORD_API}/channels/{self.channel_id}/messages",
            data=json.dumps({"content": chunk}).encode("utf-8"),
            headers={
                "Authorization": f"Bot {resolve_token(self.token)}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
            },
            method="POST",
        )
        while True:
            try:
                with urlopen(request, timeout=30) as response:  # noqa: S310
                    payload = json.loads(response.read().decode("utf-8"))
                    message_id = payload["id"]
                    if not isinstance(message_id, str):
                        raise ValueError("Discord response missing string message id")
                    return SentMessage(message_id=message_id)
            except HTTPError as error:
                if error.code != 429:
                    raise
                time.sleep(_retry_after(error.headers))


def _retry_after(headers: Message) -> float:
    value = headers.get("Retry-After")
    if value is None:
        return 1.0
    return float(value)

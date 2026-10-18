"""Minimal Discord transport used by todo approval producer and watcher."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, TypeAlias
from urllib.parse import quote
from urllib.request import Request, urlopen


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
_API: Final = "https://discord.com/api/v10"
_USER_AGENT: Final = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"


class TodoDiscordError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TodoDiscordTransport:
    token: str

    def api(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        from automation.notice_bot import resolve_token

        request = Request(
            f"{_API}{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bot {resolve_token(self.token)}",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method=method,
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed Discord HTTPS API
            body = response.read().decode("utf-8")
        try:
            return json.loads(body) if body else None
        except json.JSONDecodeError as error:
            raise TodoDiscordError("Discord response is not JSON") from error

    def post_message(self, channel_id: str, content: str) -> str:
        payload = _json_object(
            self.api("POST", f"/channels/{channel_id}/messages", {"content": content})
        )
        return _required_string(payload, "id")

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        encoded = quote(emoji, safe="")
        self.api("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me")

    def fetch_channel(self, channel_id: str) -> JsonValue:
        return self.api("GET", f"/channels/{channel_id}")

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        payload = _json_object(self.api("GET", f"/channels/{channel_id}/messages/{message_id}"))
        return _required_string(payload, "content")

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]:
        encoded = quote(emoji, safe="")
        payload = self.api(
            "GET",
            f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}",
        )
        if not isinstance(payload, list):
            raise TodoDiscordError("Discord reaction response is not a list")
        users: list[tuple[str, bool]] = []
        for value in payload:
            user = _json_object(value)
            is_bot = user.get("bot", False)
            if not isinstance(is_bot, bool):
                raise TodoDiscordError("Discord reaction bot flag is invalid")
            users.append((_required_string(user, "id"), is_bot))
        return tuple(users)

    def delete_message(self, channel_id: str, message_id: str) -> None:
        self.api("DELETE", f"/channels/{channel_id}/messages/{message_id}")


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise TodoDiscordError("Discord response is not an object")
    return value


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TodoDiscordError(f"Discord response omitted {key}")
    return value

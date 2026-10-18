"""Discord attachment transport injected into the shared submission lifecycle gate."""

from __future__ import annotations

import json
import secrets
from contextlib import closing
from dataclasses import dataclass
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from typing import Protocol, TypeAlias
from automation.typing_compat import override
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from automation.interop.approval_directory import DiscordApi

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...


_JSON_LOADS: _JsonLoader = json.loads


@dataclass(frozen=True, slots=True)
class _DiscordRequest:
    token: str
    method: str
    path: str
    body: bytes | None
    content_type: str
    timeout: float


def _request_bytes(request: _DiscordRequest) -> bytes:
    from automation.notice_bot import resolve_token

    token = resolve_token(request.token)
    if not token:
        raise SubmissionTransportError("Discord token is missing")
    try:
        with closing(HTTPSConnection("discord.com", timeout=request.timeout)) as connection:
            connection.request(
                request.method,
                f"/api/v10{request.path}",
                body=request.body,
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": request.content_type,
                    "User-Agent": _USER_AGENT,
                },
            )
            response = connection.getresponse()
            payload = response.read()
    except HTTPException as error:
        raise OSError("Discord HTTPS request failed") from error
    if not 200 <= response.status < 300:
        raise HTTPError(
            f"https://discord.com/api/v10{request.path}",
            response.status,
            str(response.reason),
            response.headers,
            None,
        )
    return payload


def _required_string(payload: dict[str, JsonValue], key: str, detail: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SubmissionTransportError(detail)
    return value

_API = "https://discord.com/api/v10"
_USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"


@dataclass(frozen=True, slots=True)
class SubmissionTransportError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class SubmissionAttachment:
    filename: str
    path: Path


@dataclass(frozen=True, slots=True)
class DiscordSubmissionMessage:
    content: str
    attachment_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscordUser:
    user_id: str
    bot: bool


class SubmissionTransport(Protocol):
    def post_submission(
        self,
        channel_id: str,
        content: str,
        attachments: tuple[SubmissionAttachment, ...],
    ) -> str: ...

    def fetch_message(self, channel_id: str, message_id: str) -> DiscordSubmissionMessage | None: ...

    def delete_message(self, channel_id: str, message_id: str) -> None: ...

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None: ...

    def reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[DiscordUser, ...]: ...


def discord_api(token: str) -> DiscordApi:
    """Build the same JSON Discord REST callable used by the approval directory."""
    def call(
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        body = _request_bytes(
            _DiscordRequest(
                token=token,
                method=method,
                path=path,
                body=None if payload is None else json.dumps(payload).encode("utf-8"),
                content_type="application/json",
                timeout=30.0,
            )
        )
        return _JSON_LOADS(body.decode("utf-8")) if body else None

    return call


@dataclass(frozen=True, slots=True)
class DiscordSubmissionTransport:
    token: str
    api: DiscordApi

    def post_submission(
        self,
        channel_id: str,
        content: str,
        attachments: tuple[SubmissionAttachment, ...],
    ) -> str:
        boundary = f"----submission{secrets.token_hex(12)}"
        metadata: dict[str, JsonValue] = {
            "content": content,
            "attachments": [
                {"id": index, "filename": attachment.filename}
                for index, attachment in enumerate(attachments)
            ],
        }
        payload_header = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            + "Content-Type: application/json\r\n\r\n"
        )
        parts = [
            payload_header.encode("utf-8"),
            json.dumps(metadata, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        ]
        for index, attachment in enumerate(attachments):
            if any(character in attachment.filename for character in ('"', "\r", "\n")):
                raise SubmissionTransportError("submission attachment filename is unsafe")
            try:
                payload = attachment.path.read_bytes()
            except OSError as error:
                raise SubmissionTransportError(
                    f"submission attachment cannot be read: {attachment.path}"
                ) from error
            attachment_header = (
                f"\r\n--{boundary}\r\nContent-Disposition: form-data; "
                + f"name=\"files[{index}]\"; filename=\"{attachment.filename}\"\r\n"
                + "Content-Type: application/octet-stream\r\n\r\n"
            )
            parts.extend((attachment_header.encode("utf-8"), payload))
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        try:
            response_body = _request_bytes(
                _DiscordRequest(
                    token=self.token,
                    method="POST",
                    path=f"/channels/{channel_id}/messages",
                    body=b"".join(parts),
                    content_type=f"multipart/form-data; boundary={boundary}",
                    timeout=120.0,
                )
            )
            raw = _JSON_LOADS(response_body.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, HTTPError, URLError) as error:
            raise SubmissionTransportError("Discord submission upload failed") from error
        if not isinstance(raw, dict):
            raise SubmissionTransportError("Discord submission upload returned no message id")
        return _required_string(raw, "id", "Discord submission upload returned no message id")

    def fetch_message(self, channel_id: str, message_id: str) -> DiscordSubmissionMessage | None:
        try:
            raw = self.api("GET", f"/channels/{channel_id}/messages/{message_id}")
        except HTTPError as error:
            if error.code == 404:
                return None
            raise SubmissionTransportError("Discord submission message lookup failed") from error
        if not isinstance(raw, dict):
            raise SubmissionTransportError("Discord submission message is malformed")
        content = raw.get("content")
        attachments = raw.get("attachments")
        if not isinstance(content, str) or not isinstance(attachments, list):
            raise SubmissionTransportError("Discord submission message is malformed")
        names: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise SubmissionTransportError("Discord submission attachment metadata is malformed")
            names.append(
                _required_string(
                    attachment,
                    "filename",
                    "Discord submission attachment metadata is malformed",
                )
            )
        return DiscordSubmissionMessage(content, tuple(names))

    def delete_message(self, channel_id: str, message_id: str) -> None:
        try:
            _ = self.api("DELETE", f"/channels/{channel_id}/messages/{message_id}")
        except HTTPError as error:
            if error.code != 404:
                raise SubmissionTransportError("Discord submission delete failed") from error

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        try:
            _ = self.api(
                "PUT",
                f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji)}/@me",
            )
        except (OSError, HTTPError, URLError) as error:
            raise SubmissionTransportError("Discord submission reaction setup failed") from error

    def reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[DiscordUser, ...]:
        try:
            raw = self.api(
                "GET",
                f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji)}?limit=100",
            )
        except HTTPError as error:
            if error.code == 404:
                return ()
            raise SubmissionTransportError("Discord submission reaction lookup failed") from error
        if not isinstance(raw, list):
            raise SubmissionTransportError("Discord submission reaction response is malformed")
        users: list[DiscordUser] = []
        for user in raw:
            if not isinstance(user, dict):
                raise SubmissionTransportError("Discord submission reaction user is malformed")
            user_id = _required_string(user, "id", "Discord submission reaction user is malformed")
            users.append(DiscordUser(user_id, bool(user.get("bot", False))))
        return tuple(users)

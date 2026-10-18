"""Stdlib Discord transport for the ops-owned repair approval boundary."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from contextlib import AbstractContextManager
from typing import Protocol, TypeAlias
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from automation.interop.approval_surface import ApprovalBinding, ApprovalSurfaceError, ChannelDirectory, LiveRequest, validate_stored_binding
from automation.repair.repair_ops_binding import directory_for_ops, new_binding, stored_binding
from automation.repair.repair_ops_pending import PendingRepairApproval

DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class DiscordResponse(Protocol):
    """Typed subset of the stdlib response used by the Discord transport."""

    def read(self) -> bytes: ...


class JsonLoader(Protocol):
    """Narrow json.loads at the Discord response boundary."""

    def __call__(self, s: bytes) -> JsonValue: ...


JSON_LOADS: JsonLoader = json.loads


def _open_discord(request: Request) -> AbstractContextManager[DiscordResponse]:
    return urlopen(request, timeout=30)


class RepairDiscordError(RuntimeError):
    """Discord could not provide an unambiguous approval request or reaction view."""


@dataclass(frozen=True, slots=True)
class RepairDiscordApi:
    """Keep readers unbound until a stored request supplies a validated destination."""

    token: str
    _binding: ApprovalBinding | None
    directory: ChannelDirectory
    owner_id: str

    @property
    def binding(self) -> ApprovalBinding:
        """Unbound bootstraps cannot read or write an arbitrary approval surface."""
        if self._binding is None:
            raise RepairDiscordError("repair transport requires a stored approval binding")
        return self._binding

    def post_approval(self, content: str) -> str:
        """Post one sanitized repair request and return its immutable message identifier."""
        return self.post_message(self.binding.channel_id, content)

    def post_message(self, channel_id: str, content: str) -> str:
        """Post non-interactive text only to this validated stored surface."""
        if channel_id != self.binding.channel_id:
            raise RepairDiscordError("Discord target differs from the stored binding")
        payload = self._mapping(
            self._api("POST", f"/channels/{channel_id}/messages", {"content": content})
        )
        return self._required(payload, "id")

    def add_reaction(self, message_id: str, emoji: str) -> None:
        """Pre-add one terminal emoji so the owner can decide with one tap."""
        _ = self._api("PUT", f"/channels/{self.binding.channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}/@me")

    def fetch_channel(self, channel_id: str) -> object:
        """Read channel metadata for server-aware approval reminder links."""
        return self._api("GET", f"/channels/{channel_id}")

    def content(self, message_id: str) -> str:
        """Read the original request before accepting any associated reaction."""
        payload = self._mapping(self._api("GET", f"/channels/{self.binding.channel_id}/messages/{message_id}"))
        return self._required(payload, "content")

    def delete_message(self, message_id: str) -> None:
        """Remove a superseded request so this ticket keeps exactly one live message."""
        try:
            _ = self._api("DELETE", f"/channels/{self.binding.channel_id}/messages/{message_id}")
        except HTTPError as error:
            if error.code != 404:
                raise

    def reaction_users(self, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        """Return only typed reactor facts; a missing emoji is an empty reaction set."""
        try:
            response = self._api(
                "GET",
                f"/channels/{self.binding.channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}?limit=100",
            )
        except HTTPError as error:
            if error.code == 404:
                return ()
            raise
        if not isinstance(response, list):
            raise RepairDiscordError("Discord reaction response is not a list")
        users: list[tuple[str, bool]] = []
        for raw_user in response:
            users.append((self._required(self._mapping(raw_user), "id"), bool(self._mapping(raw_user).get("bot", False))))
        return tuple(users)

    def assert_surface(self, binding: ApprovalBinding) -> None:
        """Reject a channel whose Discord facts contradict the stored surface."""
        try:
            _ = validate_stored_binding(binding, self.directory, self.owner_id)
        except ApprovalSurfaceError as error:
            raise RepairDiscordError("configured repair approval surface is invalid") from error

    def for_pending(self, pending: PendingRepairApproval) -> RepairDiscordApi:
        """Bind operations to a validated stored record instead of current routing."""
        try:
            binding = stored_binding(pending, self.directory, self.owner_id)
        except ApprovalSurfaceError as error:
            raise RepairDiscordError("stored repair approval surface is invalid") from error
        self.assert_surface(binding)
        return RepairDiscordApi(self.token, binding, self.directory, self.owner_id)

    def _api(self, method: str, path: str, payload: dict[str, str] | None = None) -> JsonValue:
        from automation.notice_bot import resolve_token

        request = Request(
            f"{DISCORD_API}{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bot {resolve_token(self.token)}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method=method,
        )
        with _open_discord(request) as response:
            body = response.read()
        decoded = JSON_LOADS(body) if body else None
        return decoded

    @staticmethod
    def _mapping(raw: JsonValue) -> dict[str, JsonValue]:
        if not isinstance(raw, dict):
            raise RepairDiscordError("Discord response is not an object")
        return raw

    @staticmethod
    def _required(payload: dict[str, JsonValue], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise RepairDiscordError(f"Discord response omitted {key}")
        return value


def configured_discord(
    ticket_id: str | None = None,
    outstanding: Iterable[LiveRequest] = (),
) -> RepairDiscordApi:
    """Construct the production transport only from explicitly injected ops credentials.

    ``ticket_id`` is passed by the one caller that is about to POST a request, so
    that request gets its own thread; it hands over that ticket's live pending
    record as ``outstanding`` so a re-request returns to the thread that request
    already opened instead of opening an empty one. A reader passes nothing and
    rebinds to the stored record, because resolving a request thread opens one.
    """
    return configured_setup().resolve(ticket_id, outstanding)


@dataclass(frozen=True, slots=True)
class RepairDiscordSetup:
    """자격증명만 보관한다. 표면 해석은 카드 preflight 뒤에 실행한다."""

    token: str
    owner_id: str

    def resolve(
        self, ticket_id: str | None = None, outstanding: Iterable[LiveRequest] = (),
    ) -> RepairDiscordApi:
        try:
            directory = directory_for_ops(self.token, self.owner_id)
            if ticket_id is None:
                return RepairDiscordApi(self.token, None, directory, self.owner_id)
            binding = new_binding(directory, self.owner_id, ticket_id, outstanding)
        except ApprovalSurfaceError as error:
            raise RepairDiscordError("repair approval surface cannot be resolved") from error
        discord = RepairDiscordApi(self.token, binding, directory, self.owner_id)
        discord.assert_surface(binding)
        return discord


def configured_setup() -> RepairDiscordSetup:
    """네트워크 없이 기존 필수 자격증명을 읽고 누락을 거부한다."""
    from automation.notice_bot import resolve_token

    token = resolve_token()
    owner_id = os.environ.get("AUTOPHAGY_OWNER_ID", "")
    if not token or not owner_id:
        raise RepairDiscordError("repair approval Discord credential or owner identity is missing")
    return RepairDiscordSetup(token, owner_id)

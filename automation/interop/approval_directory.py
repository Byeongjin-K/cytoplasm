"""The sole approval-path Discord ``ChannelDirectory`` resolver.

AS-1.3 exempts only this module from the approval-path resolver guard: it may
open the owner DM, read the approval-channel config keys, consult the cache,
scan guilds, (v7) find-or-create the per-kind approval threads under the
``agent_chat_channel_id`` channel, (2026-09-01) open per-request approval
threads there, and (2026-09-09) post the one announcement message a per-request
thread is anchored on — a thread opened directly on the channel renders as a
contentless "started a thread" line, so the announcement is what makes a waiting
decision visible at all. It posts nothing else: approval cards stay the
producers' to write, inside the thread. AS-3.2 retired the per-flow ``*_APPROVALS_CHANNEL_ID`` compatibility
branch, so an approval surface is now resolved from the config key, the cache or a
guild scan and from nothing else — no caller can name an environment variable to
point one somewhere. The exemption is intentionally narrower than the whole
repository: six non-approval DM senders remain outside this directory —
``procure_review.send_review``, ``cost-report.send_cost_report``,
``interop.gate_driver.main``, ``interop.hermes_plugin._send_direct_result``,
``reminder_poller.DmSender.send``, and ``research_trends._send_dm``.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol, TypeAlias
from urllib.request import Request, urlopen

from automation.interop.approval_surface import (
    ApprovalKind,
    ApprovalSurfaceError,
    ChannelFacts,
    RequestThread,
    kind_thread_name,
    request_thread_name,
    request_thread_notice,
    request_thread_title,
)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_DISCORD_API: Final = "https://discord.com/api/v10"
_INTEROP_CONFIG: Final = "~/.hermes/interop/config.json"
_OWNER_DM_PATH: Final = "/users/@me/channels"
_USER_AGENT: Final = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
_CACHE_FINGERPRINT_LENGTH: Final = 16


class DiscordApi(Protocol):
    def __call__(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue: ...


def _unbound_api(
    method: str,
    path: str,
    payload: dict[str, JsonValue] | None = None,
) -> JsonValue:
    del method, path, payload
    raise ApprovalSurfaceError("Discord API was not bound")


def _stdlib_api(token: str) -> DiscordApi:
    def call(
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        request = Request(
            f"{_DISCORD_API}{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method=method,
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - Discord HTTPS endpoint
            body = response.read().decode("utf-8")
        return json.loads(body) if body else None

    return call


@dataclass(frozen=True, slots=True)
class DiscordChannelDirectory:
    """Resolve concrete, verified-later Discord channel ids for one bot identity."""

    token: str | None
    owner_id: str
    api: DiscordApi = field(default=_unbound_api, repr=False, compare=False)
    cache_path: Path | None = None
    _owner_dm_channel_id: str | None = field(default=None, init=False, repr=False, compare=False)
    _approval_guild_id: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.owner_id:
            raise ApprovalSurfaceError("Discord directory requires an owner id")
        if self.api is _unbound_api:
            if not self.token:
                raise ApprovalSurfaceError("Discord directory requires a bot token or injected API")
            object.__setattr__(self, "api", _stdlib_api(self.token))
        object.__setattr__(self, "_approval_guild_id", self._cached_guild())

    def owner_dm(self) -> str:
        """Open and memoise this bot's owner DM without writing it to disk."""
        if self._owner_dm_channel_id is not None:
            return self._owner_dm_channel_id
        response = self._request("POST", _OWNER_DM_PATH, {"recipient_id": self.owner_id})
        channel_id = _required_string(_json_object(response, "owner DM"), "id", "owner DM")
        object.__setattr__(self, "_owner_dm_channel_id", channel_id)
        return channel_id

    def skill_approvals(self) -> str:
        """Resolve the configured, cached, or uniquely scanned skill approval channel."""
        configured = _configured_approvals_channel()
        if configured is not None:
            return configured
        cached = self._cached_approvals_channel()
        if cached is not None:
            return cached
        channel_id = self._scan_for_approvals()
        self._write_cache(channel_id)
        return channel_id

    def agent_chat(self) -> str:
        """The configured owner-agent chat channel — config key only, fail closed."""
        configured = _configured_agent_chat_channel()
        if configured is None:
            raise ApprovalSurfaceError(
                "agent_chat_channel_id is not configured in the interop config",
            )
        return configured

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        """Find or create this kind's approval thread under the agent-chat channel.

        Reuse order is deterministic: an active thread, then a public archived one
        (posting into it un-archives it), then a fresh channel thread with a 7-day
        auto-archive window. Matching is by parent channel, frozen name, and thread
        type — a same-name thread under another channel never matches.
        """
        channel_id = self.agent_chat()
        name = kind_thread_name(kind)
        guild_id = _required_string(
            _json_object(self._request("GET", f"/channels/{channel_id}"), "agent-chat channel"),
            "guild_id",
            "agent-chat channel",
        )
        self._remember_guild(guild_id)
        active = _matching_thread(
            self._request("GET", f"/guilds/{guild_id}/threads/active"), channel_id, name,
        )
        if active is not None:
            return active
        archived = _matching_thread(
            self._request("GET", f"/channels/{channel_id}/threads/archived/public"),
            channel_id,
            name,
        )
        if archived is not None:
            return archived
        created = self._request(
            "POST",
            f"/channels/{channel_id}/threads",
            {"name": name, "auto_archive_duration": 10080, "type": 11},
        )
        return _required_string(
            _json_object(created, "agent-chat thread"), "id", "agent-chat thread",
        )

    def agent_chat_request_thread(self, kind: ApprovalKind, request: RequestThread) -> str:
        """Create this request's own thread under the agent-chat channel (S6).

        An instruction message that lives in agent-chat anchors the thread — the same
        thread a result notice would open, so a 400 ("already has a thread") means the
        message id doubles as the thread id. Any other origin is ignored on this
        owner-only surface; the request then announces itself in agent-chat and hangs
        the thread on THAT message, so the channel always shows a decision is waiting
        (2026-09-09 owner report; repair ticket t_e23d85a1).
        Nothing is looked up: one request never shares a thread with another.
        """
        channel_id = self.agent_chat()
        name = request_thread_name(kind, request)
        on_instruction = bool(request.origin_message_id and request.origin_channel_id == channel_id)
        anchor = (
            request.origin_message_id if on_instruction
            else self._announce_request(channel_id, kind, request)
        )
        try:
            created = self._request(
                "POST",
                f"/channels/{channel_id}/messages/{anchor}/threads",
                {"name": name, "auto_archive_duration": 10080},
            )
        except ApprovalSurfaceError as error:
            if _http_status(error) == 400:
                object.__setattr__(self, "_approval_guild_id", self._cached_guild())
                return anchor
            raise
        body = _json_object(created, "request thread")
        thread_id = _required_string(body, "id", "request thread")
        self._remember_guild(body.get("guild_id"))
        if on_instruction:
            self._post_title(thread_id, kind, request)
        return thread_id

    def _post_title(self, thread_id: str, kind: ApprovalKind, request: RequestThread) -> None:
        """Open a thread hung on the owner's own message with the big title line.

        The announcement already carries it when the thread hangs on that announcement.
        A title is a convenience: failing to post it never blocks the approval card.
        """
        try:
            self._request("POST", f"/channels/{thread_id}/messages",
                          {"content": request_thread_title(kind, request)})
        except ApprovalSurfaceError as error:
            print(f"THREAD-TITLE-FAIL thread={thread_id} err={type(error).__name__}", file=sys.stderr)

    def _announce_request(
        self, channel_id: str, kind: ApprovalKind, request: RequestThread
    ) -> str:
        """Post the request announcement the thread will hang on, and return its id.

        Every request now anchors on a real message, so #agent-chat shows what is waiting
        instead of a contentless "started a thread" line. Fails closed like every other
        call here: a channel that refuses the announcement would refuse the thread too.
        """
        posted = self._request(
            "POST",
            f"/channels/{channel_id}/messages",
            {"content": request_thread_notice(kind, request)},
        )
        return _required_string(_json_object(posted, "request notice"), "id", "request notice")

    def describe(self, channel_id: str) -> ChannelFacts:
        """Return parsed channel facts or refuse an unverifiable Discord response."""
        body = _json_object(self._request("GET", f"/channels/{channel_id}"), "channel description")
        channel_type = body.get("type")
        if isinstance(channel_type, bool) or not isinstance(channel_type, int):
            raise ApprovalSurfaceError("channel description omitted an integer type")
        name = body.get("name")
        if name is None:
            channel_name = ""
        elif isinstance(name, str):
            channel_name = name
        else:
            raise ApprovalSurfaceError("channel description has an invalid name")
        recipients = body.get("recipients", [])
        if not isinstance(recipients, list):
            raise ApprovalSurfaceError("channel description has invalid recipients")
        recipient_ids = tuple(
            _required_string(_json_object(recipient, "channel recipient"), "id", "channel recipient")
            for recipient in recipients
        )
        parent = body.get("parent_id")
        if parent is not None and not isinstance(parent, str):
            raise ApprovalSurfaceError("channel description has an invalid parent")
        guild_id = _optional_guild_id(body.get("guild_id"))
        if channel_type in (11, 12) and parent == _configured_agent_chat_channel():
            # 좌표는 생성 응답 또는 계정 캐시에서만 온다. 검증 GET은 표면만 검증한다.
            guild_id = self._approval_guild_id
        return ChannelFacts(channel_type, channel_name, recipient_ids, parent, guild_id)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        try:
            return self.api(method, path, payload)
        except ApprovalSurfaceError:
            raise
        except Exception as error:  # noqa: BLE001 - injected REST boundary must fail closed
            # Redaction contract: `api` is an injected DiscordApi callable (e.g. skills/mail's
            # triage_binding passes triage_confirm._api), so its exception text is untrusted
            # and may embed a request url or a credential.
            # Only the cause TYPE and the integer HTTP status may cross this boundary — never
            # str/repr(error), .url, .reason, .headers or any response body.
            name = type(error).__name__
            status = ""
            if isinstance(error, urllib.error.HTTPError):
                status = f" http_status={error.code}"
            raise ApprovalSurfaceError(
                f"Discord request failed: {method} {path} cause={name}{status}",
            ) from error

    def _cached_approvals_channel(self) -> str | None:
        cache = self._read_cache()
        if not cache or (
            "approvals_channel_id" not in cache
            and _optional_guild_id(cache.get("approval_guild_id")) is not None
        ):
            return None
        return _required_string(cache, "approvals_channel_id", "approval cache")

    def _read_cache(self) -> dict[str, JsonValue]:
        if self.cache_path is None or self.token is None:
            return {}
        try:
            text = self.cache_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise ApprovalSurfaceError(f"approval cache is unreadable: {self.cache_path}") from error
        try:
            cache = _json_object(json.loads(text), "approval cache")
        except json.JSONDecodeError as error:
            raise ApprovalSurfaceError(f"approval cache is malformed: {self.cache_path}") from error
        fingerprint = cache.get("token_fingerprint")
        if fingerprint != self._token_fingerprint():
            return {}
        return cache

    def _scan_for_approvals(self) -> str:
        guilds = self._request("GET", "/users/@me/guilds")
        if not isinstance(guilds, list):
            raise ApprovalSurfaceError("guild list response is malformed")
        matches: list[str] = []
        for guild in guilds:
            guild_id = _required_string(_json_object(guild, "guild"), "id", "guild")
            channels = self._request("GET", f"/guilds/{guild_id}/channels")
            if not isinstance(channels, list):
                raise ApprovalSurfaceError("guild channel list response is malformed")
            for channel in channels:
                details = _json_object(channel, "guild channel")
                if details.get("type") == 0 and details.get("name") == "approvals":
                    matches.append(_required_string(details, "id", "approvals channel"))
        if len(matches) != 1:
            raise ApprovalSurfaceError("approvals channel is absent or ambiguous across guilds")
        return matches[0]

    def _cached_guild(self) -> str | None:
        try:
            return _optional_guild_id(self._read_cache().get("approval_guild_id"))
        except (ApprovalSurfaceError, UnicodeError):
            return None  # 선택 좌표의 캐시 실패는 승인 표면 실패가 아니다.

    def _remember_guild(self, raw: JsonValue) -> None:
        guild_id = _optional_guild_id(raw)
        try:
            if guild_id is not None:
                self._write_cache(guild_id=guild_id)
        except (ApprovalSurfaceError, UnicodeError):
            guild_id = None  # 캐시 실패는 미상으로 남기고 승인 게시를 계속한다.
        object.__setattr__(self, "_approval_guild_id", guild_id)

    def _write_cache(self, channel_id: str | None = None, *, guild_id: str | None = None) -> None:
        if self.cache_path is None or self.token is None:
            return
        payload = {**self._read_cache(), "token_fingerprint": self._token_fingerprint()}
        if channel_id is not None:
            payload["approvals_channel_id"] = channel_id
        if guild_id is not None:
            if "approval_guild_id" in payload and _optional_guild_id(payload["approval_guild_id"]) is None:
                raise ApprovalSurfaceError("approval guild cache has invalid metadata")
            payload["approval_guild_id"] = guild_id
        try:
            self.cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if guild_id is not None:
                self.cache_path.parent.chmod(0o700)
            self.cache_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            self.cache_path.chmod(0o600)
        except OSError as error:
            raise ApprovalSurfaceError(f"approval cache cannot be written: {self.cache_path}") from error

    def _token_fingerprint(self) -> str:
        if self.token is None:
            raise ApprovalSurfaceError("tokenless injected API has no cache fingerprint")
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:_CACHE_FINGERPRINT_LENGTH]


def _configured_approvals_channel() -> str | None:
    return _interop_config_string("personal_approvals_channel_id")


def _configured_agent_chat_channel() -> str | None:
    return _interop_config_string("agent_chat_channel_id")


def _interop_config_string(key: str) -> str | None:
    path = Path(os.environ.get("INTEROP_CONFIG", _INTEROP_CONFIG)).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ApprovalSurfaceError(f"interop config is unreadable: {path}") from error
    try:
        config = _json_object(json.loads(text), "interop config")
    except json.JSONDecodeError as error:
        raise ApprovalSurfaceError(f"interop config is malformed: {path}") from error
    value = config.get(key)
    if value is None:
        return None
    return _required_string(config, key, "interop config")


def _http_status(error: ApprovalSurfaceError) -> int | None:
    """The integer HTTP status ``_request`` preserved as the cause, if any."""
    cause = error.__cause__
    return cause.code if isinstance(cause, urllib.error.HTTPError) else None


def _matching_thread(listing: JsonValue, channel_id: str, name: str) -> str | None:
    body = _json_object(listing, "thread listing")
    threads = body.get("threads", [])
    if not isinstance(threads, list):
        raise ApprovalSurfaceError("thread listing response is malformed")
    for thread in threads:
        details = _json_object(thread, "thread")
        if (
            details.get("parent_id") == channel_id
            and details.get("name") == name
            and details.get("type") in (11, 12)
        ):
            return _required_string(details, "id", "thread")
    return None


def _json_object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ApprovalSurfaceError(f"{context} response is not an object")
    return value


def _optional_guild_id(value: JsonValue) -> str | None:
    """Optional Discord coordinates degrade to unknown, never to a DM guess."""
    match value:
        case str() as guild_id if guild_id.isascii() and guild_id.isdigit():
            return guild_id
        case _:
            return None


def _required_string(payload: dict[str, JsonValue], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ApprovalSurfaceError(f"{context} omitted {key}")
    return value

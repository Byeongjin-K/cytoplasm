"""Shared Discord reaction transport, ✅→gate-record transcription and thread reuse.

plaud_sync and memory_relocate each carried a byte-identical copy of this transport
and of ``record_push_approval``. The fork was never a design choice: importing
``memory_relocate.effects_live`` drags the whole memory_curator chain into the plaud
watcher, so the cheap escape was to copy. Owning the code here removes the reason —
this module imports NOTHING from memory_curator, memory_relocate or plaud_sync, so
both watchers depend on one implementation and a fix to the reaction read (429
budget, 404-as-MISSING, the bot-flag check) lands in both at once instead of one.
"""

from __future__ import annotations

import fcntl
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from http.client import HTTPSConnection
from pathlib import Path
from typing import Final, TypeAlias, TypeVar
from urllib.error import HTTPError
from urllib.parse import quote

from automation.interop.discord_transport import _retry_after

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)

_T = TypeVar("_T")

_DISCORD_API: Final = "https://discord.com/api/v10"
_USER_AGENT: Final = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
_DISCORD_MAX_ATTEMPTS: Final = 3


def record_push_approval(
    approval_log: Path,
    *,
    action_hash: str,
    target_id: str,
    owner_id: str,
    message_id: str,
    now: datetime | None = None,
) -> None:
    """Transcribe the owner's ✅ into the record the external-effect gate accepts.

    The Obsidian push is a denylisted mutation, so ``write_note`` refuses until the
    gate finds an owner approval for THAT exact tool call. The composite action hash
    binds relpath+title+body — precisely the push payload — so the ✅ probed on the
    bound message authorises this note byte-for-byte. Only ever called after a gate
    read a real owner reaction on the bound message; the append is flock-guarded
    because two watcher ticks may transcribe into the same log.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    payload = {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": message_id,
            "method": "manual_reaction",
            "owner_id": owner_id,
        },
        "hash": action_hash,
        "result": {"status": "approved"},
        "target_id": target_id,
        "timestamp": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    approval_log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with approval_log.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _ = handle.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        )
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    approval_log.chmod(0o600)


def thread_candidates(
    live: tuple[_T, ...],
    *,
    approval_thread_id: str | None,
    rebind: Callable[[str], _T],
) -> tuple[_T, ...]:
    """Where this request's card belongs: its live request, else the thread it opened.

    ``reuse_request_thread`` only ever inspects the candidates the caller hands it, so
    a request whose approval message went missing (a re-post after a renderer change, a
    card deleted by hand) offers NO candidate and resolves a brand-new binding — a
    second thread for one request, against the approval-lifecycle rule. The record still
    remembers the thread it opened, so rebinding it onto that id keeps one request on
    one thread. Record-agnostic on purpose: the two watchers share no record type.
    """
    if live:
        return live
    if approval_thread_id:
        return (rebind(approval_thread_id),)
    return ()


DiscordTransportError = ValueError


class DiscordTransport:
    """The reaction-approval subset of the Discord REST API, with a bounded 429 budget.

    Not the report transport: that one owns chunked sends, this one owns the approval
    surface (post, seed reactions, read who reacted, re-read or delete the card). The
    attempt cap is explicit so a rate-limited watcher tick ends instead of spinning.
    """

    token: str
    owner_id: str
    sleeper: Callable[[float], None]
    max_attempts: int

    def __init__(
        self,
        token: str,
        owner_id: str,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        max_attempts: int = _DISCORD_MAX_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            raise DiscordTransportError("Discord max attempts must be positive")
        from automation.notice_bot import resolve_token

        self.token = resolve_token(token)
        self.owner_id = owner_id
        self.sleeper = sleeper
        self.max_attempts = max_attempts

    def api(
        self, method: str, path: str, payload: dict[str, JsonValue] | None = None
    ) -> JsonValue:
        from automation.notice_bot import resolve_token

        attempt = 0
        while True:
            attempt += 1
            connection = HTTPSConnection("discord.com", timeout=30)
            try:
                connection.request(
                    method,
                    f"/api/v10{path}",
                    body=None if payload is None else json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bot {resolve_token(self.token)}",
                        "Content-Type": "application/json",
                        "User-Agent": _USER_AGENT,
                    },
                )
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                if response.status >= 400:
                    raise HTTPError(
                        f"{_DISCORD_API}{path}",
                        response.status,
                        response.reason,
                        response.headers,
                        None,
                    )
            except HTTPError as error:
                if error.code != 429 or attempt >= self.max_attempts:
                    raise
                self.sleeper(_retry_after(error.headers))
                continue
            finally:
                connection.close()
            break
        try:
            return json.loads(body) if body else None
        except json.JSONDecodeError as error:
            raise DiscordTransportError("Discord response is not valid JSON") from error

    def post_message(self, channel_id: str, content: str) -> str:
        return _required_string(
            _json_object(
                self.api("POST", f"/channels/{channel_id}/messages", {"content": content})
            ),
            "id",
        )

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        _ = self.api(
            "PUT",
            f"/channels/{channel_id}/messages/{message_id}/reactions/"
            f"{quote(emoji, safe='')}/@me",
        )

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        """The card's current content, or ``None`` when it is gone (404 = MISSING)."""
        try:
            return _required_string(
                _json_object(
                    self.api("GET", f"/channels/{channel_id}/messages/{message_id}")
                ),
                "content",
            )
        except HTTPError as error:
            if error.code == 404:
                return None
            raise

    def get_reaction_users(
        self, channel_id: str, message_id: str, emoji: str
    ) -> tuple[tuple[str, bool], ...]:
        """Every reactor as ``(id, is_bot)`` — the gate must not read its own seed as ✅."""
        payload = self.api(
            "GET",
            f"/channels/{channel_id}/messages/{message_id}/reactions/"
            f"{quote(emoji, safe='')}",
        )
        if not isinstance(payload, list):
            raise DiscordTransportError("Discord reaction users response is not a list")
        users: list[tuple[str, bool]] = []
        for raw_user in payload:
            user = _json_object(raw_user)
            is_bot = user.get("bot", False)
            if not isinstance(is_bot, bool):
                raise DiscordTransportError("Discord reaction user has an invalid bot flag")
            users.append((_required_string(user, "id"), is_bot))
        return tuple(users)

    def delete_message(self, channel_id: str, message_id: str) -> None:
        _ = self.api("DELETE", f"/channels/{channel_id}/messages/{message_id}")


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise DiscordTransportError("Discord response is not an object")
    return value


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise DiscordTransportError(f"Discord response omitted {key}")
    return value

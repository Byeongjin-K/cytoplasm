"""Release posting transaction: v7 card only; older versions retain detail-first replay."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Final, final

from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalSurfaceError,
    PostedApproval,
)
from automation.interop.approval_directory import DiscordApi
from automation.interop.approval_types import ApprovalRequest, Probe
from automation.interop.owner_message import LinkStatus, discord_link
from automation.interop.origin_notice import ThreadOutcome
from automation.release_card import CARD_REFUSED_PREFIX, finalize_card
from automation.release_spec import ReleaseSpec
from automation.skill_gate_approval import GateSurface, SkillApprovalGate
from automation.skill_gate_surface import JsonValue

_WIRE_ERRORS: Final = (OSError, ValueError, KeyError, TypeError)
_POST_ERRORS: Final = (*_WIRE_ERRORS, ApprovalSurfaceError)


def _snowflake(value: JsonValue) -> str | None:
    return (
        value
        if isinstance(value, str)
        and value.isascii()
        and value.isdecimal()
        and bool(value.strip("0"))
        and not value.startswith("0")
        and len(value) <= 20
        else None
    )


def existing_thread(api: DiscordApi, channel_id: str, version: str) -> str | None:
    """Find this release's thread under its approval channel, active or archived."""
    try:
        raw = api("GET", f"/channels/{channel_id}")
    except _WIRE_ERRORS:
        raw = None
    guild_id = _snowflake(raw.get("guild_id")) if isinstance(raw, dict) else None
    paths = ([f"/guilds/{guild_id}/threads/active"] if guild_id else []) + [
        f"/channels/{channel_id}/threads/archived/public",
    ]
    failure: Exception | None = None
    for path in paths:
        try:
            raw = api("GET", path)
        except _WIRE_ERRORS as error:
            failure = error
            continue
        threads = raw.get("threads") if isinstance(raw, dict) else None
        for thread in threads if isinstance(threads, list) else []:
            if not isinstance(thread, dict) or thread.get("parent_id") != channel_id:
                continue
            name = str(thread.get("name") or "")
            for outcome in ThreadOutcome:
                name = name.removeprefix(f"{outcome.value} · ")
            if name == f"🏷️ 릴리스 {version}":
                found = _snowflake(thread.get("id"))
                if found is not None:
                    return found
    if failure is not None:
        raise failure
    return None


@final
class ReleaseRequestGate:
    """Mutable transaction state so commit records the exact linked card that was posted."""

    def __init__(self, gate: SkillApprovalGate, api: DiscordApi) -> None:
        self._gate = gate
        self._api = api

    @property
    def surface(self) -> GateSurface:
        return self._gate.surface

    @property
    def spec(self) -> ReleaseSpec:
        current = self._gate.spec
        if not isinstance(current, ReleaseSpec):
            raise ApprovalSurfaceError("release request gate requires a release spec")
        return current

    def channel_id(self) -> str:
        return self._gate.channel_id()

    def stored(self) -> dict[str, str] | None:
        return self._gate.stored()

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        return self._gate.outstanding(key)

    def probe(self, request: ApprovalRequest) -> Probe:
        return self._gate.probe(request)

    def delete(self, request: ApprovalRequest) -> None:
        self._gate.delete(request)

    def drop(self, request: ApprovalRequest) -> None:
        self._gate.drop(request)

    def path(self) -> Path:
        return self._gate.path()

    def new_record(self, posted: PostedApproval) -> dict[str, str]:
        return self._gate.new_record(posted)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        # Owner 2026-10-18 23:04/23:07 KST: a release approval opens its own thread under the
        # approval channel; the card's reactions are read there (the binding accepts a thread
        # only when its live parent is that pinned channel).
        # Owner 2026-10-19 00:00 KST: once that work's thread is open, later cards stay in it —
        # a replaced request for the same version posts into the same thread.
        thread_id = self._existing_thread(intent.channel_id)
        created = thread_id is None
        if thread_id is None:
            thread_id = self._open_thread(intent.channel_id)
        try:
            return self._post_in(thread_id)
        except ApprovalSurfaceError:
            if created:
                self._delete_thread(thread_id)
            raise

    def _thread_name(self) -> str:
        return f"🏷️ 릴리스 {self.spec.version}"

    def _existing_thread(self, channel_id: str) -> str | None:
        try:
            return existing_thread(self._api, channel_id, self.spec.version)
        except _WIRE_ERRORS:
            return None

    def _open_thread(self, channel_id: str) -> str:
        payload: dict[str, JsonValue] = {
            "name": self._thread_name(),
            "type": 11,
            "auto_archive_duration": 10080,
        }
        try:
            raw = self._api("POST", f"/channels/{channel_id}/threads", payload)
        except _POST_ERRORS as error:
            raise ApprovalSurfaceError(f"release thread was not opened: {type(error).__name__}") from error
        thread_id = _snowflake(raw.get("id")) if isinstance(raw, dict) else None
        if thread_id is None:
            raise ApprovalSurfaceError("release thread response carries no id")
        return thread_id

    def _delete_thread(self, thread_id: str) -> None:
        try:
            self._api("DELETE", f"/channels/{thread_id}")
        except _POST_ERRORS as error:
            print(f"RELEASE-THREAD-CLEANUP-FAIL {type(error).__name__}", file=sys.stderr)

    def _post_in(self, channel_id: str) -> PostedApproval:
        messages = self.spec.detail_messages()
        detail_ids = self._post_details(channel_id, messages)
        guild_id = self._guild_id(channel_id)
        linked = replace(
            self.spec,
            posted_text="",
            detail_message_ids=detail_ids,
            detail_channel_id=channel_id,
            detail_guild_id=guild_id,
        )
        card, refusal = finalize_card(linked)
        if card is None:
            self._delete_details(channel_id, detail_ids)
            raise ApprovalSurfaceError(refusal.removeprefix(f"{CARD_REFUSED_PREFIX} "))
        self._gate = replace(self._gate, spec=card)
        payload: dict[str, JsonValue] = {"content": card.render()}
        if detail_ids:
            payload["message_reference"] = {
                "message_id": detail_ids[0],
                "fail_if_not_exists": True,
            }
            payload["allowed_mentions"] = {"parse": [], "replied_user": False}
        try:
            raw = self._api("POST", f"/channels/{channel_id}/messages", payload)
            card_id = _snowflake(raw.get("id")) if isinstance(raw, dict) else None
            if card_id is None:
                raise ApprovalSurfaceError("approval post response carries no message id")
        except _POST_ERRORS as error:
            self._delete_details(channel_id, detail_ids)
            raise ApprovalSurfaceError(type(error).__name__) from error
        if self.spec.render_version == 6:
            self._backlink_details(
                channel_id, guild_id, card_id, messages, detail_ids
            )
        return PostedApproval(card_id, channel_id)

    def commit(
        self, intent: ApprovalIntent, posted: PostedApproval, created_at: str
    ) -> None:
        self._gate.commit(intent, posted, created_at)

    def _post_details(
        self, channel_id: str, messages: tuple[str, ...]
    ) -> tuple[str, ...]:
        posted: list[str] = []
        for message in messages:
            try:
                raw = self._api(
                    "POST", f"/channels/{channel_id}/messages", {"content": message}
                )
                raw_id = raw.get("id") if isinstance(raw, dict) else None
                message_id = _snowflake(raw_id)
                if message_id is None:
                    if isinstance(raw_id, str) and raw_id:
                        posted.append(raw_id)
                    raise ApprovalSurfaceError("detail post response carries no message id")
                posted.append(message_id)
            except _POST_ERRORS as error:
                self._delete_details(channel_id, tuple(posted))
                print(
                    (
                        f"RELEASE-DETAIL-POST-FAIL {type(error).__name__}"
                        f" posted={len(posted)}/{len(messages)}"
                    ),
                    file=sys.stderr,
                )
                raise ApprovalSurfaceError(type(error).__name__) from error
        return tuple(posted)

    def _guild_id(self, channel_id: str) -> str:
        try:
            raw = self._api("GET", f"/channels/{channel_id}")
        except _WIRE_ERRORS:
            return ""
        guild_id = _snowflake(raw.get("guild_id")) if isinstance(raw, dict) else None
        return guild_id or ""

    def _delete_details(self, channel_id: str, message_ids: tuple[str, ...]) -> None:
        for message_id in message_ids:
            try:
                _ = self._api(
                    "DELETE", f"/channels/{channel_id}/messages/{message_id}"
                )
            except _WIRE_ERRORS as error:
                print(
                    f"RELEASE-DETAIL-CLEANUP-FAIL {type(error).__name__}",
                    file=sys.stderr,
                )

    def _backlink_details(
        self,
        channel_id: str,
        guild_id: str,
        card_id: str,
        messages: tuple[str, ...],
        message_ids: tuple[str, ...],
    ) -> None:
        link = discord_link(
            space="guild" if guild_id else "unknown",
            guild_id=guild_id or None,
            channel_id=channel_id,
            message_id=card_id,
        )
        reference = (
            link.url
            if link.status is LinkStatus.AVAILABLE and link.url is not None
            else f"검색: 승인 카드 / {self.spec.version} {self.spec.head_sha[:12]}"
        )
        for message, message_id in zip(messages, message_ids, strict=True):
            try:
                _ = self._api(
                    "PATCH",
                    f"/channels/{channel_id}/messages/{message_id}",
                    {"content": f"{message}\n-# 승인 카드 → {reference}"},
                )
            except _WIRE_ERRORS as error:
                print(
                    f"RELEASE-DETAIL-BACKLINK-FAIL {type(error).__name__}",
                    file=sys.stderr,
                )

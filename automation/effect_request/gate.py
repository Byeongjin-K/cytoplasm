"""Shared-lifecycle approval adapter and Discord runtime for generic effect requests.

Mirrors ``obsidian_write.note_request_gate``: the façade owns lease → probe → supersede →
post → commit; this adapter maps it onto ``EffectRequestStore`` and the shared reaction
transport. Only an owner (non-bot) reaction on the bound card counts.
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final
from urllib.error import HTTPError

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRequest,
    ApprovalSurfaceError,
    Outcome,
    PostedApproval,
    Probe,
    Reason,
    Verdict,
    request_owner_approval,
)
from automation.interop.approval_surface import (
    ApprovalKind,
    RequestThread,
    resolve_new_binding,
    reuse_request_thread,
)
from automation.interop.reaction_approval import DiscordTransport

from .model import DRIVE_SAVE, EffectRequest, EffectRequestError, EffectRequestStore
from .render import RenderError, render_effect_approval, render_effect_list, version_for

APPROVE_EMOJI: Final = "\u2705"
CANCEL_EMOJI: Final = "\u26d4"
_TRANSPORT_ERRORS: Final = (OSError, ValueError, KeyError, TypeError, RuntimeError, HTTPError)
_THREAD_LOOK: Final = {DRIVE_SAVE: ("Drive 저장", "💾")}
#: A record may get a card only before one is bound and before the owner decided anything.
_POSTABLE: Final = frozenset({"proposed", "posted", "superseded"})


def _owner_reacted(users: tuple[tuple[str, bool], ...], owner_id: str) -> bool:
    return any(user_id == owner_id and not is_bot for user_id, is_bot in users)


@dataclass(frozen=True, slots=True)
class EffectApprovalGate:
    record: EffectRequest
    store: EffectRequestStore
    transport: DiscordTransport
    journal: PostingJournal | None = None

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        return tuple(
            ApprovalRequest(record.key, record.action_hash, record.message_id, record.channel_id, record.created_at)
            for record in self.store.all()
            if record.key == key and record.message_id and record.status in {"posted", "approved"}
        )

    def probe(self, request: ApprovalRequest) -> Probe:
        try:
            content = self.transport.get_message(request.channel_id, request.message_id)
            if content is None:
                return Probe.MISSING
            if request.action_hash not in content:
                return Probe.BINDING_MISMATCH
            cancelled = self.transport.get_reaction_users(request.channel_id, request.message_id, CANCEL_EMOJI)
            approved = self.transport.get_reaction_users(request.channel_id, request.message_id, APPROVE_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error
        if _owner_reacted(cancelled, self.transport.owner_id):
            return Probe.CANCELLED
        listing = self._listing(request)
        if listing is not None:
            return listing
        if _owner_reacted(approved, self.transport.owner_id):
            return Probe.APPROVED
        return Probe.BOUND_PENDING

    def _listing(self, request: ApprovalRequest) -> Probe | None:
        """A tree card counts only while every list message under it is there, word for word.

        The owner approves the list he can read; a list message that is gone or changed
        leaves nothing to approve, so the card is treated as gone (MISSING) or not ours.
        """
        record = next((item for item in self.store.all() if item.message_id == request.message_id), self.record)
        expected = render_effect_list(record)
        if not expected:
            return None
        ids = tuple(record.list_message_ids)
        if len(ids) != len(expected):
            return Probe.MISSING
        try:
            for message_id, body in zip(ids, expected, strict=True):
                content = self.transport.get_message(request.channel_id, message_id)
                if content is None:
                    return Probe.MISSING
                if content != body:
                    return Probe.BINDING_MISMATCH
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error
        return None

    def delete(self, request: ApprovalRequest) -> None:
        try:
            self.transport.delete_message(request.channel_id, request.message_id)
        except HTTPError as error:
            if error.code != 404:
                raise ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        self.store.clear_message(request.key, request.action_hash, request.message_id)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        current = self.store.get(self.record.request_id)
        if current is not None and (current.message_id is not None or current.status not in _POSTABLE):
            # Bound or decided since it was read (an overlapping copy): never a second card.
            raise ApprovalSurfaceError(f"effect request is already {current.status}")
        try:
            content = render_effect_approval(self.record)
            listing = render_effect_list(self.record)
            message_id = self.transport.post_message(intent.channel_id, content)
            try:
                list_ids = tuple(self.transport.post_message(intent.channel_id, chunk) for chunk in listing)
            except _TRANSPORT_ERRORS:
                # A tree card is approved only with its whole list beside it: no list, no card.
                with suppress(*_TRANSPORT_ERRORS):
                    self.transport.delete_message(intent.channel_id, message_id)
                raise
            if self.journal is not None:
                self.journal.enrich(intent.key, intent.action_hash, message_id, intent.channel_id)
            self.store.set_message(self.record.request_id, message_id, intent.channel_id,
                                   version_for(self.record), list_ids)
            if self.journal is not None:
                self.journal.clear(intent.key)
        except (*_TRANSPORT_ERRORS, EffectRequestError, RenderError) as error:
            raise ApprovalSurfaceError(str(error)) from error
        for emoji in (APPROVE_EMOJI, CANCEL_EMOJI):
            try:
                self.transport.add_reaction(intent.channel_id, message_id, emoji)
            except _TRANSPORT_ERRORS:
                continue
        return PostedApproval(message_id, intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        del intent, created_at
        self.store.set_message(self.record.request_id, posted.message_id, posted.channel_id,
                               version_for(self.record))


@dataclass(frozen=True, slots=True)
class DiscordRuntime:
    transport: DiscordTransport
    directory: DiscordChannelDirectory
    owner_id: str


def discord_runtime(token: str, owner_id: str, store: EffectRequestStore) -> DiscordRuntime:
    if not token or not owner_id:
        raise EffectRequestError("Discord token or owner id is unavailable")
    transport = DiscordTransport(token, owner_id)
    directory = DiscordChannelDirectory(token, owner_id, transport.api, store.root / "approval-directory.json")
    return DiscordRuntime(transport, directory, owner_id)


def request_approval(record: EffectRequest, store: EffectRequestStore, runtime: DiscordRuntime) -> Verdict:
    """Bind the per-request thread (one key keeps one thread) and post through the façade."""
    # A copy of the same request may have posted this record's card since it was read.
    record = store.get(record.request_id) or record
    if record.status not in _POSTABLE:
        return Verdict(Outcome.DEFERRED, Reason.OWNER_DECIDED)
    live = EffectApprovalGate(record, store, runtime.transport).outstanding(record.key)
    label, icon = _THREAD_LOOK.get(record.effect, ("", ""))
    binding = reuse_request_thread(
        ApprovalKind.EXTERNAL_EFFECT, live, runtime.directory, runtime.owner_id
    ) or resolve_new_binding(
        ApprovalKind.EXTERNAL_EFFECT,
        runtime.directory,
        runtime.owner_id,
        request=RequestThread(
            title=record.summary,
            origin_channel_id=record.origin_channel_id,
            origin_message_id=record.origin_message_id,
            label=label,
            icon=icon,
        ),
    )
    bound = replace(
        record,
        kind=binding.kind.value,
        surface=binding.surface.value,
        channel_id=binding.channel_id,
        policy_version=binding.policy_version,
        approval_thread_id=binding.channel_id,
        approval_guild_id=binding.guild_id or record.approval_guild_id,
    )
    try:
        store.update(bound, expect=record.status)
    except EffectRequestError:
        # Settled (done, cancelled, ...) while the thread was resolved: keep that result, post nothing.
        fresh = store.get(record.request_id)
        if fresh is not None and fresh.status not in _POSTABLE:
            return Verdict(Outcome.DEFERRED, Reason.OWNER_DECIDED)
        raise
    journal = PostingJournal(store.root / "posting-journal")
    gate = EffectApprovalGate(bound, store, runtime.transport, journal)
    intent = ApprovalIntent(key=bound.key, action_hash=bound.action_hash, channel_id=binding.channel_id)
    return request_owner_approval(intent, gate, FileKeyLease(store.root / "approval-leases"), journal)


def owner_id_from_config() -> str:
    path = Path(os.environ.get("INTEROP_CONFIG", str(Path.home() / ".hermes" / "interop" / "config.json")))
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("owner_id")
    except (OSError, ValueError, AttributeError):
        value = None
    if not isinstance(value, str) or not value:
        raise EffectRequestError("interop config has no owner_id (fail-closed)")
    return value

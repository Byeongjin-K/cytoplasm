"""Shared-lifecycle approval adapter and Discord runtime for project proposals.

Mirrors ``obsidian_write.note_request_gate``: the façade owns lease → probe → supersede →
post → commit; this adapter maps it onto ``ProposalStore`` and the shared reaction
transport. Only an owner (non-bot) reaction on the bound card that still carries the
action hash counts.
"""

from __future__ import annotations

import json
import os
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
    PostedApproval,
    Probe,
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

from .models import ProjectBoardError
from .proposal_render import RENDER_VERSION, RenderError, render_proposal_card
from .proposals import ProposalRecord, ProposalStore

APPROVE_EMOJI: Final = "\u2705"
CANCEL_EMOJI: Final = "\u26d4"
_TRANSPORT_ERRORS: Final = (OSError, ValueError, KeyError, TypeError, RuntimeError, HTTPError)


def live_requests(store: ProposalStore, key: str) -> tuple[ApprovalRequest, ...]:
    return tuple(
        ApprovalRequest(record.key, record.action_hash, record.message_id, record.channel_id, record.created_at)
        for record in store.all()
        if record.key == key and record.message_id and record.status in {"posted", "approved"}
    )


def _owner_reacted(users: tuple[tuple[str, bool], ...], owner_id: str) -> bool:
    return any(user_id == owner_id and not is_bot for user_id, is_bot in users)


@dataclass(frozen=True, slots=True)
class ProposalApprovalGate:
    record: ProposalRecord
    store: ProposalStore
    transport: DiscordTransport
    journal: PostingJournal | None = None

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        return live_requests(self.store, key)

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
        if _owner_reacted(approved, self.transport.owner_id):
            return Probe.APPROVED
        return Probe.BOUND_PENDING

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
        try:
            content = render_proposal_card(self.record, self.store.frozen(self.record))
            message_id = self.transport.post_message(intent.channel_id, content)
            if self.journal is not None:
                self.journal.enrich(intent.key, intent.action_hash, message_id, intent.channel_id)
                self.store.set_message(self.record.request_id, message_id, intent.channel_id, RENDER_VERSION)
                self.journal.clear(intent.key)
        except (*_TRANSPORT_ERRORS, ProjectBoardError, RenderError) as error:
            raise ApprovalSurfaceError(str(error)) from error
        for emoji in (APPROVE_EMOJI, CANCEL_EMOJI):
            try:
                self.transport.add_reaction(intent.channel_id, message_id, emoji)
            except _TRANSPORT_ERRORS:
                continue
        return PostedApproval(message_id, intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        del intent, created_at
        self.store.set_message(self.record.request_id, posted.message_id, posted.channel_id, RENDER_VERSION)


@dataclass(frozen=True, slots=True)
class DiscordRuntime:
    transport: DiscordTransport
    directory: DiscordChannelDirectory
    owner_id: str


def discord_runtime(token: str, owner_id: str, store: ProposalStore) -> DiscordRuntime:
    if not token or not owner_id:
        raise ProjectBoardError("Discord token or owner id is unavailable")
    transport = DiscordTransport(token, owner_id)
    directory = DiscordChannelDirectory(token, owner_id, transport.api, store.root / "approval-directory.json")
    return DiscordRuntime(transport, directory, owner_id)


def thread_title(record: ProposalRecord) -> str:
    return f"{record.project} · 할 일 {record.todo_count}건"


def request_approval(record: ProposalRecord, store: ProposalStore, runtime: DiscordRuntime) -> Verdict:
    """Bind the per-request thread and post through the façade.

    A revision of a still-pending card reuses that card's thread (same key); a revision
    after ⛔ gets a fresh thread, because the cancelled one is closed and renamed.
    """
    binding = reuse_request_thread(
        ApprovalKind.PROJECT_BOARD, live_requests(store, record.key), runtime.directory, runtime.owner_id
    ) or resolve_new_binding(
        ApprovalKind.PROJECT_BOARD,
        runtime.directory,
        runtime.owner_id,
        request=RequestThread(
            title=thread_title(record),
            origin_channel_id=record.origin_channel_id,
            origin_message_id=record.origin_message_id,
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
    store.update(bound)
    journal = PostingJournal(store.root / "posting-journal")
    gate = ProposalApprovalGate(bound, store, runtime.transport, journal)
    intent = ApprovalIntent(key=bound.key, action_hash=bound.action_hash, channel_id=binding.channel_id)
    return request_owner_approval(intent, gate, FileKeyLease(store.root / "approval-leases"), journal)


def owner_id_from_config() -> str:
    path = Path(os.environ.get("INTEROP_CONFIG", str(Path.home() / ".hermes" / "interop" / "config.json")))
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("owner_id")
    except (OSError, ValueError, AttributeError):
        value = None
    if not isinstance(value, str) or not value:
        raise ProjectBoardError("interop config has no owner_id (fail-closed)")
    return value

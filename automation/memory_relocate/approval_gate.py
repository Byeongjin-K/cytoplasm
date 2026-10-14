"""Owner-DM approval adapter for one memory relocation record."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final
from urllib.error import HTTPError

from .approval_api import DiscordTransportLike, RelocationStoreLike, lifecycle
from .model import RelocationRecord, record_key
from .render import prepare_approval_card, prepare_batch_card

if TYPE_CHECKING:
    from automation.interop.approval_lease import ApprovalLease, PostingJournal
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalRequest,
        PostedApproval,
        Probe,
        Verdict,
    )
    from automation.interop.approval_surface import ApprovalBinding

APPROVE_EMOJI: Final = "\u2705"
CANCEL_EMOJI: Final = "\u26d4"
_TRANSPORT_ERRORS: Final = (OSError, ValueError, KeyError, TypeError, RuntimeError)
def approval_key(record: RelocationRecord) -> str:
    """One lifecycle key per card: the entry for a single move, the batch for a batched one."""
    if record.batch_hash is not None:
        return f"batch:{record.batch_hash}"
    return record_key(record.source_kind, record.entry_sha256)


def approval_hash(record: RelocationRecord) -> str:
    return record.batch_hash or record.action_hash


#: Keycap reactions 1️⃣…9️⃣ and 🔟: the owner's number on a batch card keeps that line where it is.
KEYCAPS: Final = (*(f"{number}\ufe0f\u20e3" for number in range(1, 10)), "\U0001f51f")


def dropped_lines(
    read_users: Callable[[str], tuple[tuple[str, bool], ...]], owner_id: str, count: int
) -> frozenset[int]:
    """Card numbers (1-based) the owner reacted to with a keycap; bots and others do not count."""
    return frozenset(
        number
        for number, emoji in enumerate(KEYCAPS[:count], start=1)
        if _owner_reacted(read_users(emoji), owner_id)
    )


def _owner_reacted(users: tuple[tuple[str, bool], ...], owner_id: str) -> bool:
    return any(user_id == owner_id and not is_bot for user_id, is_bot in users)


@dataclass(frozen=True, slots=True)
class RelocateApprovalGate:
    """Adapt relocation persistence and Discord I/O to ``ApprovalGate``."""

    record: RelocationRecord
    entry_text: str
    store: RelocationStoreLike
    transport: DiscordTransportLike
    journal: PostingJournal | None = None
    card: tuple[str, str] | None = None
    #: Every (record, entry text) one batch card covers; empty for a single move.
    members: tuple[tuple[RelocationRecord, str], ...] = ()

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        request_type = lifecycle().ApprovalRequest
        requests = {
            (record.message_id, record.channel_id): request_type(
                key=key,
                action_hash=approval_hash(record),
                message_id=record.message_id,
                channel_id=record.channel_id,
                created_at=record.created_at,
            )
            for record in self.store.pending()
            if approval_key(record) == key and record.message_id
        }
        return tuple(requests.values())

    def probe(self, request: ApprovalRequest) -> Probe:
        state = lifecycle().Probe
        content = self._content(request)
        if content is None:
            return state.MISSING
        if request.action_hash not in content:
            return state.BINDING_MISMATCH
        try:
            self.transport.add_reaction(
                request.channel_id,
                request.message_id,
                APPROVE_EMOJI,
            )
            self.transport.add_reaction(
                request.channel_id,
                request.message_id,
                CANCEL_EMOJI,
            )
            cancelled = self.transport.get_reaction_users(
                request.channel_id,
                request.message_id,
                CANCEL_EMOJI,
            )
            approved = self.transport.get_reaction_users(
                request.channel_id,
                request.message_id,
                APPROVE_EMOJI,
            )
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if _owner_reacted(cancelled, self.transport.owner_id):
            return state.CANCELLED
        if _owner_reacted(approved, self.transport.owner_id):
            return state.APPROVED
        return state.BOUND_PENDING

    def _content(self, request: ApprovalRequest) -> str | None:
        try:
            return self.transport.get_message(request.channel_id, request.message_id)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def delete(self, request: ApprovalRequest) -> None:
        try:
            self.transport.delete_message(request.channel_id, request.message_id)
        except HTTPError as error:
            if error.code != 404:
                raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        """Request a compare-and-swap unbind of the exact stored binding."""
        if self.record.batch_hash is not None:
            self.store.clear_batch_message_id(self.record.batch_hash, request.message_id)
            return
        self.store.clear_message_id(
            request.key,
            request.action_hash,
            request.message_id,
        )

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        version, content = self.card or prepare_approval_card(self.record, self.entry_text)
        try:
            message_id = self.transport.post_message(intent.channel_id, content)
            if self.journal is not None:
                self.journal.enrich(
                    intent.key,
                    intent.action_hash,
                    message_id,
                    intent.channel_id,
                )
                for member in self._bound():
                    self.store.set_message_id(
                        replace(member, render_version=version),
                        message_id,
                        intent.channel_id,
                    )
                self.journal.clear(intent.key)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        for emoji in (APPROVE_EMOJI, CANCEL_EMOJI):
            try:
                self.transport.add_reaction(intent.channel_id, message_id, emoji)
            except _TRANSPORT_ERRORS:
                continue
        return lifecycle().PostedApproval(message_id, intent.channel_id)

    def commit(
        self,
        intent: ApprovalIntent,
        posted: PostedApproval,
        created_at: str,
    ) -> None:
        """Bind the message id AND its channel through the store's no-overwrite operation."""
        del intent, created_at
        for member in self._bound():
            self.store.set_message_id(member, posted.message_id, posted.channel_id)

    def _bound(self) -> tuple[RelocationRecord, ...]:
        if not self.members:
            return (self.record,)
        version = self.record.render_version
        return tuple(replace(member, render_version=version) for member, _ in self.members)


def request_approval(
    record: RelocationRecord,
    entry_text: str,
    *,
    store: RelocationStoreLike,
    transport: DiscordTransportLike,
    binding: ApprovalBinding,
    lease: ApprovalLease,
    journal: PostingJournal,
    card: tuple[str, str] | None = None,
    members: tuple[tuple[RelocationRecord, str], ...] = (),
) -> Verdict:
    """Request one lifecycle-managed owner approval on the injected binding.

    With ``members`` (records sharing ``record.batch_hash``) the card covers all of them and one ✅
    approves the set; the lifecycle key and hash are the batch's.
    """
    gate = RelocateApprovalGate(record, entry_text, store, transport, journal, card, members)
    key = approval_key(record)
    if not gate.outstanding(key):
        selected = card or (prepare_batch_card(members) if members else prepare_approval_card(record, entry_text))
        gate = replace(gate, record=replace(record, render_version=selected[0]), card=selected)
    shared = lifecycle()
    intent = shared.ApprovalIntent(
        key=key,
        action_hash=approval_hash(record),
        channel_id=binding.channel_id,
    )
    return shared.request_owner_approval(
        intent,
        gate,
        lease,
        journal,
    )

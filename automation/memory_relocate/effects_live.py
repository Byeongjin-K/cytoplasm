from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final, assert_never

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import ApprovalRequest, Probe
from automation.interop.approval_surface import (
    ApprovalKind,
    RequestThread,
    resolve_new_binding,
    reuse_request_thread,
)
from automation.interop.reaction_approval import (
    DiscordTransport,
    DiscordTransportError,
    record_push_approval,
    thread_candidates,
)
from automation.memory_curator.binding import entry_digest
from automation.memory_curator.watch_steps import read_native
from automation.obsidian_write.config import ObsidianWriteError, load_config

from .approval_gate import RelocateApprovalGate, approval_hash, approval_key, dropped_lines, request_approval
from .apply import ApplyOutcome
from .choice_accounts import accounts
from .choice_agent import AgentChoices
from .choice_delete import NativeDelete
from .choice_verify import Verifier
from .choices import Choice, RENDER_VERSION as CHOICES_VERSION, ROW_REACTIONS
from .model import RelocationRecord, record_key
from .move_effects import MoveContext, build_move_effects
from .render import prepare_approval_card, prepare_batch_card
from .relocation_store import RelocationStore, RelocationStoreError
from .watch_step import ResolveEffects

_EFFECT_ERRORS: Final = (OSError, ValueError, RuntimeError, ObsidianWriteError)

#: Re-exported for the callers and tests that reached for them here while this module
#: still owned the transport; ``automation.interop.reaction_approval`` is the source.
__all__ = [
    "DiscordTransport",
    "DiscordTransportError",
    "RelocationStore",
    "RelocationStoreError",
    "build_effects",
    "recover_entry_text",
    "record_push_approval",
]



def recover_entry_text(memory_dir: Path, record: RelocationRecord) -> str | None:
    """Return the exact current native entry only when its bound digest still matches."""
    _, memory_file = read_native(memory_dir, record.source_kind)
    return next((entry.text for entry in memory_file.entries if entry_digest(record.source_kind, entry.text) == record.entry_sha256), None)




def build_effects(*, memory_dir: Path, state_path: Path, rag_state_path: Path, token: str, owner_id: str, now: datetime) -> ResolveEffects:
    """Assemble the production retrying effects without bypassing any approval gate."""
    store = RelocationStore(state_path)
    transport = DiscordTransport(token, owner_id)
    directory = DiscordChannelDirectory(token, owner_id, transport.api, state_path.parent / "approval-directory.json")
    lease = FileKeyLease(state_path.parent / "approval-leases")
    journal = PostingJournal(state_path.parent / "posting-journal")
    write_config = load_config()
    push_approval_log = state_path.parent / "relocate-push-approvals.jsonl"
    choice_agent: AgentChoices | None = None

    def choices() -> AgentChoices:
        nonlocal choice_agent
        if choice_agent is None:
            choice_agent = AgentChoices(
                store, NativeDelete(memory_dir, state_path.parent / "choice-deletes", now),
                accounts(), Verifier(transport, directory),
            )
        return choice_agent


    def live_requests(key: str) -> tuple[RelocationRecord, ...]:
        """The records whose approval message is live for this key — the gate's own read."""
        try:
            return tuple(
                pending
                for pending in store.all_pending()
                if approval_key(pending) == key
                and pending.message_id
            )
        except _EFFECT_ERRORS:
            return ()

    def post(record: RelocationRecord) -> tuple[str, str] | None:
        try:
            text = recover_entry_text(memory_dir, record)
            if text is None:
                return None
            members = batch_members(record)
            if record.batch_hash is not None and members is None:
                return None
            key = approval_key(record)
            live = live_requests(key)
            card = None if live else (
                prepare_batch_card(members) if members else prepare_approval_card(record, text)
            )
            # One approval key keeps ONE thread: this runs before the façade decides
            # PENDING, so a live request of the same key lends its thread instead of
            # leaving an empty one behind per tick. A request whose message went MISSING
            # has no live candidate at all, so the record's own thread is offered too —
            # otherwise the re-post resolves a fresh binding and opens a SECOND thread.
            # On the owner's private surface the thread names the note under review,
            # using only its filename — never directories or the entry text being moved.
            binding = reuse_request_thread(
                ApprovalKind.OBSIDIAN_WRITE,
                thread_candidates(
                    live,
                    approval_thread_id=record.approval_thread_id,
                    rebind=lambda thread_id: replace(record, channel_id=thread_id),
                ),
                directory,
                owner_id,
            ) or resolve_new_binding(
                ApprovalKind.OBSIDIAN_WRITE,
                directory,
                owner_id,
                request=RequestThread(
                    title=f"메모리 정리 {len(members)}건" if members else PurePosixPath(record.note_relpath).name
                ),
            )
            bound = replace(
                record, approval_thread_id=binding.channel_id,
                approval_guild_id=binding.guild_id or record.approval_guild_id,
            )
            store.update(bound)
            if members:
                members = tuple(
                    (replace(member, approval_thread_id=binding.channel_id,
                             approval_guild_id=binding.guild_id or member.approval_guild_id), member_text)
                    for member, member_text in members
                )
                for member, _ in members:
                    if member.entry_sha256 != record.entry_sha256:
                        store.update(member)
            verdict = request_approval(
                bound,
                text,
                card=card,
                store=store,
                transport=transport,
                binding=binding,
                lease=lease,
                journal=journal,
                **({"members": members} if members else {}),
            )
        except _EFFECT_ERRORS:
            return None
        if verdict.posted is not None:
            return verdict.posted.message_id, verdict.posted.channel_id
        if verdict.live is not None:
            return verdict.live.message_id, verdict.live.channel_id
        return None

    decided: dict[str, str] = {}
    dropped: dict[str, frozenset[int]] = {}

    def probe(record: RelocationRecord) -> str:
        if record.render_version == CHOICES_VERSION:
            count = sum(member.batch_hash == record.batch_hash for member in store.all_pending())
            for emoji in (*(emoji for row in ROW_REACTIONS[:count] for emoji in row), "✅", "⛔"):
                transport.add_reaction(record.channel_id, record.message_id or "", emoji)
            return choices().probe(record)
        batch = record.batch_hash
        if batch is None:
            return probe_one(record)
        if batch not in decided:
            decided[batch] = probe_one(record)
            if decided[batch] == "approved":
                try:
                    count = max((m.batch_index or 0) for m in store.all_pending() if m.batch_hash == batch)
                    channel, message = record.channel_id, record.message_id or ""
                    dropped[batch] = dropped_lines(
                        lambda emoji: transport.get_reaction_users(channel, message, emoji), owner_id, count
                    )
                except _EFFECT_ERRORS:
                    decided[batch] = "pending"
        verdict = decided[batch]
        if verdict == "approved" and record.batch_index in dropped.get(batch, frozenset()):
            return "cancelled"
        return verdict

    def probe_one(record: RelocationRecord) -> str:
        try:
            text = recover_entry_text(memory_dir, record)
            # An unbound surface cannot be verified; fail closed rather than treating it as gone and discarding ✅.
            if text is None or record.message_id is None or not record.channel_id:
                return "pending"
            request = ApprovalRequest(
                approval_key(record),
                approval_hash(record),
                record.message_id,
                record.channel_id,
                record.created_at,
            )
            decision = RelocateApprovalGate(record, text, store, transport).probe(request)
        except _EFFECT_ERRORS:
            return "pending"
        match decision:  # noqa: F401  # noqa: MATCH_OK - Probe is statically exhaustive.
            case Probe.APPROVED:
                return "approved"
            case Probe.CANCELLED:
                return "cancelled"
            case Probe.MISSING:
                return "missing"
            case Probe.BOUND_PENDING | Probe.BINDING_MISMATCH | Probe.UNVERIFIABLE:
                return "pending"


    def batch_members(record: RelocationRecord) -> tuple[tuple[RelocationRecord, str], ...] | None:
        """Every pending member of the record's batch with its current text, or None if one drifted."""
        if record.batch_hash is None:
            return ()
        members: list[tuple[RelocationRecord, str]] = []
        for member in sorted(store.all_pending(), key=lambda pending: (pending.batch_index or 0, record_key(pending.source_kind, pending.entry_sha256))):
            if member.batch_hash != record.batch_hash:
                continue
            member_text = recover_entry_text(memory_dir, member)
            if member_text is None:
                return None
            members.append((member, member_text))
        return tuple(members)


    moves = build_move_effects(
        MoveContext(memory_dir, rag_state_path, write_config, push_approval_log, owner_id, now),
        recover_entry_text,
    )
    def write(record: RelocationRecord) -> tuple[str, str] | None:
        if record.render_version == CHOICES_VERSION and not choices().permits_move(record):
            return None
        return moves.write(record)

    def apply(record: RelocationRecord) -> ApplyOutcome:
        if record.render_version == CHOICES_VERSION:
            match record.choice:
                case Choice.DELETE:
                    return choices().delete(record)
                case Choice.KEEP | None:
                    return ApplyOutcome(False, "not_approved", None, 0)
                case Choice.MOVE:
                    pass
                case _:
                    assert_never(record.choice)
            if not choices().permits_move(record):
                return ApplyOutcome(False, "not_approved", None, 0)
        return moves.apply(record)

    return ResolveEffects(post, probe, write, moves.verify, apply, now)

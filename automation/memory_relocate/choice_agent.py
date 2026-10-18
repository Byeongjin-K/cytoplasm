"""Agent-owned owner choices; peer receipts attest only completed deletions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import assert_never

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.watch_steps import read_native

from .apply import ApplyOutcome
from .choice_accounts import Accounts
from .choice_delete import NativeDelete
from .choice_files import atomic_text
from .choice_request import ChoiceRequest, encode_request, parse_request
from .choice_snapshot import ApprovalReader, Snapshot, SnapshotStore, encode_snapshot, parse_snapshot
from .choices import Choice, ChoiceError
from .line_choices import RENDER_VERSION as LINE_VERSION
from .model import RelocationRecord, RelocationState, record_key
from .relocation_store import RelocationStore
from .store import load_state, save_state


@dataclass(frozen=True, slots=True)
class AgentChoices:
    store: RelocationStore
    native: NativeDelete
    accounts: Accounts
    verifier: ApprovalReader
    snapshots: SnapshotStore

    def publish_decision(self, snapshot: Snapshot) -> None:
        """Retirement must not dispatch new destructive work to an old peer watcher."""
        if self.accounts.peer is not None:
            self.accounts.agent.publish(
                f"{snapshot.request.key}.decision.json", encode_snapshot(snapshot),
            )

    def request(self, record: RelocationRecord) -> ChoiceRequest:
        # The original manifest survives native deletion and status transitions.
        key = ChoiceRequest(((record, ""),)).key
        previous = self.accounts.agent.read(f"{key}.json")
        if previous is not None:
            return parse_request(previous)
        members: list[tuple[RelocationRecord, str]] = []
        for member in sorted(self.store.all_pending(), key=lambda r: r.batch_index or 0):
            if member.batch_hash != record.batch_hash:
                continue
            _, memory = read_native(self.native.memory_dir, member.source_kind)
            matches = [entry.text for entry in memory.entries
                       if entry_digest(member.source_kind, entry.text) == member.entry_sha256]
            if len(matches) != 1:
                raise ChoiceError("source_entry_changed")
            members.append((member, matches[0]))
        request = parse_request(encode_request(ChoiceRequest(tuple(members))))
        self.accounts.agent.publish(f"{request.key}.json", encode_request(request))
        return request

    def snapshot(self, request: ChoiceRequest) -> Snapshot | None:
        if self.accounts.peer is None:
            return None
        document = self.accounts.peer.read(f"{request.key}.json")
        if document is None:
            return None
        snapshot = parse_snapshot(document)
        if encode_request(snapshot.request) != encode_request(request):
            raise ChoiceError("peer_receipt_binding")
        return snapshot

    def probe(self, record: RelocationRecord) -> str:
        request = self.request(record)
        if record.render_version == LINE_VERSION:
            snapshot = self.snapshots.freeze(request, self.verifier)
            if snapshot is None:
                return "pending"
            if self.verifier.decision(self._frozen_request(snapshot)) != "approved":
                return "pending"
            self.publish_decision(snapshot)
            self._persist_choices(snapshot)
            return "kept" if snapshot.choices[0] is Choice.KEEP else "approved"
        # Fresh cancellation stops unfinished effects without changing frozen rows.
        decision = self.verifier.decision(request)
        match decision:
            case "cancelled":
                # A denial may cross the untrusted mailbox; it can never grant authority.
                self.accounts.agent.publish(f"{request.key}.cancel.json", '{"cancelled":true}')
                previous = self.snapshots.load(request)
                if previous is not None:
                    cancelled = replace(previous, cancelled=True)
                    self.snapshots.save(cancelled)
                    self.publish_decision(cancelled)
                return "cancelled"
            case "pending":
                return "pending"
            case "approved":
                pass
            case _:
                assert_never(decision)
        snapshot = self.snapshots.freeze(request, self.verifier)
        if snapshot is None:
            return "pending"
        self.publish_decision(snapshot)
        if snapshot.cancelled:
            return "cancelled"
        match snapshot.phase:
            case "collecting":
                raise ChoiceError("snapshot-incomplete")
            case "cancelled":
                return "cancelled"
            case "approved":
                self._persist_choices(snapshot)
                choice = snapshot.choices[request.index(record)]
                match choice:
                    case Choice.KEEP:
                        return "kept"
                    case Choice.MOVE | Choice.DELETE:
                        return "approved"
                    case _:
                        assert_never(choice)
            case _:
                assert_never(snapshot.phase)

    def _persist_choices(self, snapshot: Snapshot) -> None:
        state = load_state(self.store.state_path)
        records = dict(state.relocations)
        for (approved, _), choice in zip(snapshot.request.members, snapshot.choices, strict=True):
            key = record_key(approved.source_kind, approved.entry_sha256)
            current = records[key]
            if (current.batch_hash, current.message_id, current.channel_id) != (
                approved.batch_hash, approved.message_id, approved.channel_id,
            ) or current.choice not in (None, choice):
                raise ChoiceError("local_choice_binding")
            records[key] = replace(current, choice=choice)
        if records != dict(state.relocations):
            save_state(self.store.state_path, RelocationState(state.version, records))

    def permits_move(self, record: RelocationRecord) -> bool:
        request = self.request(record)
        snapshot = self.snapshots.load(request)
        return (
            snapshot is not None and snapshot.phase == "approved" and not snapshot.cancelled
            and snapshot.choices[request.index(record)] is Choice.MOVE
            and self.verifier.decision(self._frozen_request(snapshot)) == "approved"
        )

    def _frozen_request(self, snapshot: Snapshot) -> ChoiceRequest:
        """Carry the durable line choice into message-only revalidation; never reselect."""
        if snapshot.request.record.render_version != LINE_VERSION:
            return snapshot.request
        return ChoiceRequest(tuple(
            (replace(record, choice=choice), text)
            for (record, text), choice in zip(snapshot.request.members, snapshot.choices, strict=True)
        ))

    def delete(self, record: RelocationRecord) -> ApplyOutcome:
        request = self.request(record)
        snapshot = self.snapshots.load(request)
        retired = self.accounts.peer is None
        receipt = None if retired else self.snapshot(request)
        if snapshot is not None and receipt is not None and receipt.choices != snapshot.choices:
            raise ChoiceError("peer_receipt_binding")
        if snapshot is None or (not retired and (
            receipt is None or record.entry_sha256 not in receipt.peer_done
        )):
            return ApplyOutcome(False, "peer_pending", None, 0)
        number = request.index(record)
        if snapshot.cancelled:
            return ApplyOutcome(False, "not_approved", None, 0)
        if snapshot.phase != "approved" or snapshot.choices[number] is not Choice.DELETE:
            raise ChoiceError("delete_not_authorized")
        if self.verifier.decision(self._frozen_request(snapshot)) != "approved":
            return ApplyOutcome(False, "not_approved", None, 0)
        backups = self.native.apply(request, number)
        if retired:
            atomic_text(
                self.native.receipts / f"{request.key}.{record.entry_sha256}.peer-retired.json",
                '{"peer_retired":true,"scope":"agent","status":"done"}',
            )
        return ApplyOutcome(True, None, backups[0] if backups else None, 0)

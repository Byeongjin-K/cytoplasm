"""Peer-owned reaction executor: independently approve, freeze, then delete locally."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, assert_never

from .choice_delete import NativeDelete
from .choice_files import Mailbox
from .choice_request import ChoiceRequest
from .choice_snapshot import ApprovalReader, Snapshot, SnapshotStore, encode_snapshot
from .choices import Choice, ChoiceError


@dataclass(frozen=True, slots=True)
class PeerExecutor:
    snapshots: SnapshotStore
    native: NativeDelete
    outbox: Mailbox

    def run(self, request: ChoiceRequest, verifier: ApprovalReader, *, withdrawn: bool = False) -> Snapshot | None:
        if withdrawn:
            previous = self.snapshots.load(request)
            cancelled = (replace(previous, cancelled=True) if previous is not None
                         else Snapshot(request, "cancelled", (), self.snapshots.now.isoformat(), cancelled=True))
            self.snapshots.save(cancelled)
            self.outbox.publish(f"{request.key}.json", encode_snapshot(cancelled))
            return cancelled
        snapshot = self.snapshots.freeze(request, verifier)
        if snapshot is None:
            return None
        self.outbox.publish(f"{request.key}.json", encode_snapshot(snapshot))
        match snapshot.phase:
            case "collecting":
                raise ChoiceError("snapshot-incomplete")
            case "cancelled":
                return snapshot
            case "approved":
                pass
            case _:
                assert_never(snapshot.phase)
        if snapshot.cancelled:
            return snapshot
        completed = list(snapshot.peer_done)
        for number, ((record, _), choice) in enumerate(zip(request.members, snapshot.choices, strict=True)):
            match choice:
                case Choice.MOVE | Choice.KEEP:
                    continue
                case Choice.DELETE:
                    pass
                case _:
                    assert_never(choice)
            if record.entry_sha256 in completed:
                continue
            # A frozen choice cannot change; cancellation still stops unfinished effects.
            decision = verifier.decision(request)
            match decision:
                case "cancelled":
                    snapshot = replace(snapshot, cancelled=True)
                    self.snapshots.save(snapshot)
                    self.outbox.publish(f"{request.key}.json", encode_snapshot(snapshot))
                    return snapshot
                case "pending":
                    return snapshot
                case "approved":
                    pass
                case _:
                    assert_never(decision)
            _ = self.native.apply(request, number)
            completed.append(record.entry_sha256)
            snapshot = replace(snapshot, peer_done=tuple(completed))
            self.snapshots.save(snapshot)
            self.outbox.publish(f"{request.key}.json", encode_snapshot(snapshot))
        return snapshot

    def notify(self, snapshot: Snapshot, send: Callable[[Snapshot], bool]) -> None:
        """Delivery failure cannot advance the notification watermark."""
        if snapshot.phase != "approved" or not snapshot.peer_done:
            return
        marker = self.snapshots.root / f"{snapshot.request.key}.notified"
        value = ",".join(snapshot.peer_done)
        try:
            previous = marker.read_text(encoding="utf-8")
        except FileNotFoundError:
            previous = ""
        if previous != value and send(snapshot):
            from .choice_files import atomic_text

            atomic_text(marker, value)

"""One durable peer-owned selection vector per physical approval card."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, assert_never

from .choice_files import atomic_text
from .choice_request import ChoiceRequest, encode_request, parse_request
from .choices import Choice, ChoiceError
from . import choice_json

Decision = Literal["approved", "cancelled", "pending"]


class ApprovalReader(Protocol):
    def decision(self, request: ChoiceRequest) -> Decision: ...
    def choices(self, request: ChoiceRequest) -> tuple[Choice, ...]: ...


@dataclass(frozen=True, slots=True)
class Snapshot:
    request: ChoiceRequest
    phase: Literal["collecting", "approved", "cancelled"]
    choices: tuple[Choice, ...]
    observed_at: str
    peer_done: tuple[str, ...] = ()
    cancelled: bool = False


def encode_snapshot(snapshot: Snapshot) -> str:
    return json.dumps({
        "request": encode_request(snapshot.request), "phase": snapshot.phase,
        "choices": list(snapshot.choices), "observed_at": snapshot.observed_at,
        "peer_done": list(snapshot.peer_done),
        "cancelled": snapshot.cancelled,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_snapshot(document: str) -> Snapshot:
    payload = choice_json.mapping(choice_json.decode(document))
    request = parse_request(choice_json.text(payload.get("request")))
    moment = choice_json.text(payload.get("observed_at"))
    choices = choice_json.sequence(payload.get("choices"))
    done = choice_json.sequence(payload.get("peer_done"))
    match payload.get("cancelled"):
        case bool() as cancelled:
            pass
        case _:
            raise ChoiceError("snapshot_cancelled")
    match payload.get("phase"):
        case "collecting":
            phase: Literal["collecting", "approved", "cancelled"] = "collecting"
        case "approved":
            phase = "approved"
        case "cancelled":
            phase = "cancelled"
        case _:
            raise ChoiceError("snapshot_shape")
    parsed: list[Choice] = []
    for value in choices:
        parsed.append(Choice(choice_json.text(value)))
    completed: list[str] = []
    allowed = {r.entry_sha256 for (r, _), choice in zip(request.members, parsed) if choice is Choice.DELETE}
    for value in done:
        digest = choice_json.text(value)
        if digest not in allowed or digest in completed:
            raise ChoiceError("snapshot_completion")
        completed.append(digest)
    if (phase == "approved" and len(parsed) != len(request.members)) or (phase != "approved" and parsed):
        raise ChoiceError("snapshot_count")
    return Snapshot(request, phase, tuple(parsed), moment, tuple(completed), cancelled)


@dataclass(frozen=True, slots=True)
class SnapshotStore:
    root: Path
    now: datetime

    def load(self, request: ChoiceRequest) -> Snapshot | None:
        try:
            document = (self.root / f"{request.key}.json").read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        snapshot = parse_snapshot(document)
        if encode_request(snapshot.request) != encode_request(request):
            raise ChoiceError("snapshot_request_changed")
        return snapshot

    def save(self, snapshot: Snapshot) -> None:
        atomic_text(self.root / f"{snapshot.request.key}.json", encode_snapshot(snapshot))

    def freeze(self, request: ChoiceRequest, verifier: ApprovalReader) -> Snapshot | None:
        existing = self.load(request)
        if existing is not None:
            match existing.phase:
                case "collecting":
                    raise ChoiceError("snapshot-incomplete")
                case "approved" | "cancelled":
                    return existing  # Never re-read rows, even after a restart.
                case _:
                    assert_never(existing.phase)
        decision = verifier.decision(request)
        moment = self.now.isoformat()
        match decision:
            case "pending":
                return None
            case "cancelled":
                frozen = Snapshot(request, "cancelled", (), moment)
            case "approved":
                self.save(Snapshot(request, "collecting", (), moment))
                choices = verifier.choices(request)
                # Cancel wins if it arrived during the multi-request row snapshot.
                confirmed = verifier.decision(request)
                match confirmed:
                    case "pending":
                        raise ChoiceError("snapshot-approval-removed")
                    case "cancelled":
                        frozen = Snapshot(request, "cancelled", (), moment)
                    case "approved":
                        frozen = Snapshot(request, "approved", choices, moment)
                    case _:
                        assert_never(confirmed)
            case _:
                assert_never(decision)
        self.save(frozen)
        return frozen

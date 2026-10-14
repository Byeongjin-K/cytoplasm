"""Agent decision relay: owner Q149 (2026-10-14), "peer 확인 빼고 가"."""
from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from .choice_files import Mailbox
from .choice_request import ChoiceRequest, encode_request
from .choice_snapshot import Decision, Snapshot, parse_snapshot
from .choices import Choice, ChoiceError


@dataclass(frozen=True, slots=True)
class AgentDecision:
    mailbox: Mailbox

    def snapshot(self, request: ChoiceRequest) -> Snapshot | None:
        document = self.mailbox.read(f"{request.key}.decision.json")
        if document is None:
            return None
        snapshot = parse_snapshot(document)
        if encode_request(snapshot.request) != encode_request(request):
            raise ChoiceError("agent_decision_binding")
        return snapshot

    def decision(self, request: ChoiceRequest) -> Decision:
        snapshot = self.snapshot(request)
        if snapshot is None:
            return "pending"
        if snapshot.cancelled:
            return "cancelled"
        match snapshot.phase:
            case "cancelled":
                return "cancelled"
            case "approved":
                return "approved"
            case "collecting":
                return "pending"
            case _:
                assert_never(snapshot.phase)

    def choices(self, request: ChoiceRequest) -> tuple[Choice, ...]:
        snapshot = self.snapshot(request)
        if snapshot is None or snapshot.phase != "approved" or snapshot.cancelled:
            raise ChoiceError("agent_decision_missing")
        return snapshot.choices

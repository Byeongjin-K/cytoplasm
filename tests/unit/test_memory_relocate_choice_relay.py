"""The peer's approval reader consumes only request-bound agent decisions."""
from __future__ import annotations

from dataclasses import replace
from typing import Literal

import pytest

from automation.memory_relocate.choice_snapshot import Snapshot, encode_snapshot
from automation.memory_relocate.choices import Choice
from memory_choice_fakes import NOW
from test_memory_relocate_choice_execution import Execution, execution as execution


def test_decision_when_agent_file_is_missing_stays_pending(execution: Execution) -> None:
    # Given: a request without a decision file, despite owner reactions on Discord.
    # When: the peer's approval reader is asked for its decision.
    decision = execution.reader.decision(execution.request)
    # Then: no grant is inferred from the request or any external state.
    assert decision == "pending"


@pytest.mark.parametrize("phase,cancelled,expected", [
    ("collecting", False, "pending"),
    ("cancelled", False, "cancelled"),
    ("approved", False, "approved"),
    ("approved", True, "cancelled"),
])
def test_decision_when_snapshot_has_a_phase_reads_it(
    execution: Execution, phase: Literal["collecting", "cancelled", "approved"],
    cancelled: bool, expected: str,
) -> None:
    # Given: an agent-owned snapshot with a legal phase and matching request.
    snapshot = Snapshot(execution.request, "approved", (Choice.MOVE,) * 3, NOW.isoformat())
    if phase != "approved":
        snapshot = replace(snapshot, phase=phase, choices=())
    document = encode_snapshot(replace(snapshot, cancelled=cancelled))
    execution.reader.mailbox.publish(f"{execution.request.key}.decision.json", document)
    # When: the peer consumes that snapshot.
    decision = execution.reader.decision(execution.request)
    # Then: only an approved, non-cancelled snapshot grants execution.
    assert decision == expected


def test_choices_when_agent_file_is_missing_are_empty_not_invented(execution: Execution) -> None:
    # Given: the agent has not frozen choices (the owner has not pressed yet).
    # When / Then: the peer gets no choices to execute, never an invented default vector.
    assert execution.reader.choices(execution.request) == ()

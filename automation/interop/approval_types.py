"""Dependency-neutral identity and observation types for owner approvals."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class ApprovalContext:
    """Storage identity for legacy approval records; not an approval evaluator."""

    approval_log: Path | None
    owner_id: str
    e2e_test_mode: bool


@dataclass(frozen=True, slots=True)
class ExternalEffectDecision:
    """Legacy result shape retained for callers that store or display decisions."""

    external_effect: bool
    allowed: bool
    reason: str | None
    action_hash: str
    target_id: str


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    key: str
    action_hash: str
    message_id: str
    channel_id: str
    created_at: str


class Probe(StrEnum):
    BOUND_PENDING = "bound-pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    MISSING = "missing"
    BINDING_MISMATCH = "binding-mismatch"
    UNVERIFIABLE = "unverifiable"

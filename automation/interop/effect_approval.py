"""Validate exact stored card bindings, without classifying Hermes tool calls."""
from __future__ import annotations

import fcntl
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from automation.interop.approval_types import ApprovalContext, ExternalEffectDecision, JsonValue
from automation.interop.injection_adapter import InboundEvent, accept_test_event


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Exact arguments bound by an existing card producer."""

    tool_name: str
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    action_hash: str
    target_id: str


@dataclass(frozen=True, slots=True)
class SignedApprovalEvent:
    event: InboundEvent
    signature: str
    secret: bytes


def action_hash(call: ToolCall, target_id: str) -> str:
    """Preserve the canonical bytes of already issued cards."""
    payload = {"action": "external_effect.tool_call", "arguments": call.arguments,
               "target_id": target_id, "tool_name": call.tool_name}
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def validate_approval(call: ToolCall, target_id: str, context: ApprovalContext) -> ExternalEffectDecision:
    """Check a producer-selected card target; no read/mutation policy or denylist."""
    digest = action_hash(call, target_id)
    approved = context.approval_log is not None and _has_valid_approval(
        context.approval_log, ApprovalBinding(digest, target_id), context
    )
    return ExternalEffectDecision(True, approved, "approved" if approved else "approval_required",
                                  digest, target_id)


def approval_challenge(action_hash: str, target_id: str) -> str:
    return f"APPROVE external-effect {action_hash} target:{target_id}"


def record_signed_e2e_approval(
    context: ApprovalContext, binding: ApprovalBinding, signed_event: SignedApprovalEvent,
) -> bool:
    """Retain the isolated, owner-bound test record format used by card consumers."""
    if context.approval_log is None:
        return False
    event = signed_event.event
    if not accept_test_event(event, signed_event.signature, signed_event.secret,
                             e2e_test_mode=context.e2e_test_mode):
        return False
    if event.user_id != context.owner_id or event.channel_id != "approvals":
        return False
    if event.text != approval_challenge(binding.action_hash, binding.target_id):
        return False
    _append_record(context.approval_log, {
        "action": "external_effect.approval",
        "approval": {"channel": "approvals", "message_id": event.event_id,
                     "method": "signed_injection_e2e", "owner_id": context.owner_id},
        "hash": binding.action_hash, "result": {"status": "approved"}, "target_id": binding.target_id,
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return True


def _has_valid_approval(
    approval_log: Path, binding: ApprovalBinding, context: ApprovalContext,
) -> bool:
    try:
        lines = approval_log.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        approval = record.get("approval") if isinstance(record, dict) else None
        if not isinstance(approval, dict):
            continue
        method = approval.get("method")
        e2e_allowed = method == "signed_injection_e2e" and context.e2e_test_mode
        manual_allowed = method == "manual_reaction"
        if not (e2e_allowed or manual_allowed):
            continue
        if (
            record.get("action") == "external_effect.approval"
            and record.get("hash") == binding.action_hash
            and record.get("target_id") == binding.target_id
            and record.get("result") == {"status": "approved"}
            and approval.get("channel") == "approvals"
            and approval.get("owner_id") == context.owner_id
        ):
            return True
    return False


def _append_record(path: Path, record: dict[str, JsonValue]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)

"""Owner reaction resolution against the complete persisted approval presentation."""
from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import TYPE_CHECKING

import mail_approval_attachment
import mail_card_compact

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import JsonValue


def resolve_reaction(draft: Mapping[str, JsonValue]) -> str | None:
    """Return the bound owner decision, with cancellation taking precedence."""
    # Resolve the existing facade lazily, as the lifecycle adapter does: the
    # standalone skill's facade also exports this resolver.
    triage_approval = import_module("triage_approval")
    triage_binding = import_module("triage_binding")
    confirm = import_module("triage_confirm")
    gate = import_module("triage_gate")
    GateError = gate.GateError

    if not draft.get("message_id"):
        raise GateError("드래프트가 아직 승인 메시지에 게시되지 않음 — 승인 불가", 1)
    # The watch tick retains its pre-post draft. Load the committed presentation
    # before resolving that stale snapshot; never transfer a different binding.
    if "approval_format" not in draft and gate._draft_path(str(draft["id"])) is not None:
        stored = gate.load_draft(str(draft["id"]))
        if "approval_format" in stored:
            if any(stored.get(key) != draft.get(key) for key in ("sha256", "message_id")):
                raise GateError("저장된 승인 바인딩 불일치 — 거부", 1)
            return resolve_reaction(stored)
    channel_id = triage_binding.persisted_channel_id(draft)
    if channel_id is None:
        channel_id = str(triage_approval.stored_binding(draft).channel_id)
    message = confirm._api("GET", f"/channels/{channel_id}/messages/{draft['message_id']}")
    if not isinstance(message, dict) or not mail_card_compact.references(
            draft, str(message.get("content", "")), str(draft["sha256"])):
        raise GateError("승인 메시지가 이 드래프트 해시를 참조하지 않음 — 거부", 1)
    if not mail_approval_attachment.matches(draft, message):
        raise GateError("승인 본문 첨부 검증 실패 — 거부", 1)
    owner = confirm.owner_id()
    if confirm._owner_reacted(confirm._reaction_users(channel_id, draft["message_id"], confirm.CANCEL_EMOJI), owner):
        return confirm.CANCEL_EMOJI
    if confirm._owner_reacted(confirm._reaction_users(channel_id, draft["message_id"], confirm.APPROVE_EMOJI), owner):
        return confirm.APPROVE_EMOJI
    return None

"""Entity-preflight adapter for the single mail draft execution boundary.

Runtime resolution lives in ``mail_runtime`` and prefers ``/srv/autophagy-agent-current``
before ``/srv/autophagy-agents``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import gmail_approval_gate
import mail_gmail_send
import mail_quote
import mail_runtime
import triage_core
import triage_gate
from mail_runtime import MailPreflightError, _contracts, _gate, _repo_module

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import (
        JsonValue,
        VerificationRecord,
        WriteReceipt,
    )
    from automation.entity_preflight.gate import GateDependencies, GuardRequest


def repo_root() -> Path:
    """Resolve relative to this module for the deployed-import compatibility surface."""
    return mail_runtime.repo_root(Path(__file__))


@dataclass(frozen=True, slots=True)
class _MailWriteAdapter:
    draft: Mapping[str, JsonValue]
    approval: triage_gate.Approval

    def write(self, payload: Mapping[str, JsonValue]) -> WriteReceipt:
        contracts = _contracts()
        draft = _draft_with_payload(self.draft, payload)
        triage_gate.execute_draft(draft, self.approval)
        return contracts.WriteReceipt(_external_system(draft), _text(draft, "id"), "mail.send", "executed")

    def requery(self, receipt: WriteReceipt, expected_fingerprint: str) -> VerificationRecord:
        contracts = _contracts()
        return contracts.VerificationRecord(
            external_system=receipt.external_system,
            resource_id=receipt.resource_id,
            api_operation="mail.send.verified_response",
            queried_at="verified",
            outcome=contracts.VerificationOutcome.MATCH,
            expected_fingerprint=expected_fingerprint,
            observed_fingerprint=expected_fingerprint,
            sensitive_evidence_ref="private://entity-preflight/mail-send-response",
        )


def mail_guard_request(draft: Mapping[str, JsonValue]) -> GuardRequest:
    """Build the one mail call-site request over recipient, subject, and body."""

    contracts = _contracts()
    gate = _gate()
    payload = {
        "to": _text(draft, "to"),
        "cc": _draft_cc(draft),
        "subject": _text(draft, "subject"),
        "body": _text(draft, "body"),
    }
    raw_text = "\n".join(payload.values())
    key = _fingerprint({"draft_id": _text(draft, "id"), **payload})
    return gate.GuardRequest(
        request=contracts.PreflightInput(key, raw_text, "mail", "send", ()),
        payload=payload,
        sources=(),
        idempotency_key=key,
        actor="owner",
        purpose="mail_send",
        requested_at="runtime",
    )


def ensure_cli_evidence_query(draft: Mapping[str, JsonValue]) -> bool:
    """Resolve entities; only a missing module degrades read-only evidence."""
    try:
        gate = _gate()
    except MailPreflightError:
        return False
    try:
        decision = gate._resolve(mail_guard_request(draft))
    except (OSError, RuntimeError, ValueError) as error:
        message = f"ENTITY-PREFLIGHT-FAIL code={error.__class__.__name__} — 근거를 수집하지 않았습니다."
        raise triage_gate.GateError(message, 3) from None
    if decision.needs_confirmation:
        text = _repo_module("clarify").render_clarify(decision)
        print(text)
        raise triage_gate.GateError(text, 2)
    return True


def guarded_execute_draft(
    draft: Mapping[str, JsonValue],
    approval: triage_gate.Approval,
    dependencies: GateDependencies | None = None,
) -> None:
    """Resolve mail fields immediately before the existing approval-gated sender."""

    gate = _gate()
    try:
        _ = gate.guarded_write(
            mail_guard_request(draft),
            _MailWriteAdapter(draft, approval),
            gate.production_dependencies() if dependencies is None else dependencies,
        )
    except gate.EntityClarificationRequired as error:
        raise MailPreflightError(str(error), 6, error.should_render) from None
    except gate.EntityPreflightUnavailable as error:
        raise MailPreflightError(str(error), 3) from None
    except gate.PostWriteVerificationFailed as error:
        raise MailPreflightError(str(error), 6) from None


def execute_cli_draft(draft: Mapping[str, JsonValue], approval: triage_gate.Approval) -> None:
    try:
        guarded_execute_draft(draft, approval)
    except MailPreflightError as error:
        if error.should_render:
            print(error)
        raise triage_gate.GateError(str(error), error.exit_code) from None


def _draft_with_payload(draft: Mapping[str, JsonValue], payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    snapshot = (
        gmail_approval_gate.snapshot_from_draft(dict(draft))
        if draft.get("provider") == "gmail"
        else None
    )
    updated = dict(draft)
    to = _payload_text(payload, "to")
    cc_value = payload.get("cc")
    cc = cc_value if isinstance(cc_value, str) else _draft_cc(draft)
    subject = _payload_text(payload, "subject")
    body = _payload_text(payload, "body")
    # The guard payload carries the owner-reviewed reply text alone, so rebuilding the
    # argv from it dropped the quoted original that create_draft had frozen in: a
    # forward whose quote was 3,530 chars went out with the 266-char reply only
    # (2026-09-03). The quote is the answered mail — never entity-normalized — so it is
    # re-attached below whatever reply text the preflight returns.
    quote = str(draft.get("quote") or "")
    argv = _argv_with_payload(_argv(draft), to, cc, subject, mail_quote.with_quote(body, quote))
    # The HTML body is rebuilt the same way: reviewed reply text + the frozen quote.
    updated["argv"] = list(_argv_with_payload(
        argv, to, cc, subject, body,
        body_html=mail_quote.html_body(body, str(draft.get("quote_html") or "")),
    ) if "--body-html" in argv else argv)
    updated["to"] = to
    # Only a draft that already ships a ``cc`` field gets one back: adding the key to a
    # record persisted without it would change its sha256 out from under the approval
    # message that pinned it, and the Cc itself already rides in the argv above.
    if "cc" in draft:
        updated["cc"] = cc
    updated["subject"] = subject
    updated["body"] = body
    if snapshot is not None:
        _refresh_gmail_snapshot(updated, snapshot)
    updated["sha256"] = triage_core.draft_sha256(updated)
    return updated


def _refresh_gmail_snapshot(
    draft: dict[str, JsonValue], snapshot: gmail_approval_gate.GmailApprovalSnapshot
) -> None:
    action = mail_gmail_send.CanonicalMailAction(
        account="gmail",
        argv=_argv(draft),
        attachment_manifest=tuple(
            mail_gmail_send.AttachmentManifestEntry(
                source_path_private=attachment.source_path_private,
                filename=attachment.filename,
                size_bytes=attachment.size_bytes,
                mime_type=attachment.mime_type,
                sha256=attachment.sha256,
            )
            for attachment in snapshot.attachments
        ),
        attachment_manifest_sha256=snapshot.attachment_manifest_sha256,
    )
    normalized = gmail_approval_gate.build_approval_snapshot(action)
    attachments: list[JsonValue] = []
    for attachment in normalized.attachments:
        attachments.append(
            {
                "display_name": attachment.filename,
                "mime_type": attachment.mime_type,
                "sha256": attachment.sha256,
                "size_bytes": attachment.size_bytes,
                "source_path_private": attachment.source_path_private,
            }
        )
    draft["approval_action_hash"] = normalized.action_hash
    draft["attachments"] = attachments
    draft["attachment_manifest_sha256"] = normalized.attachment_manifest_sha256
    draft["gmail_approval_snapshot"] = normalized.record()


def _argv_with_payload(
    argv: tuple[str, ...], to: str, cc: str, subject: str, body: str, *, body_html: str | None = None,
) -> tuple[str, ...]:
    updated = list(argv)
    pairs = (("--to", to), ("--cc", cc), ("--subject", subject))
    pairs += (("--body-html", body_html),) if body_html is not None else (("--body", body),)
    for option, value in pairs:
        if option in updated:
            index = updated.index(option) + 1
            if index >= len(updated):
                raise MailPreflightError(f"메일 argv에 {option} 값이 없습니다", 3)
            updated[index] = value
    return tuple(updated)


def _argv(draft: Mapping[str, JsonValue]) -> tuple[str, ...]:
    value = draft.get("argv")
    if not isinstance(value, list):
        raise MailPreflightError("드래프트 argv 형식이 올바르지 않습니다", 3)
    argv = tuple(item for item in value if isinstance(item, str))
    if len(argv) != len(value):
        raise MailPreflightError("드래프트 argv 형식이 올바르지 않습니다", 3)
    return argv


def _draft_cc(draft: Mapping[str, JsonValue]) -> str:
    """The Cc this draft ships: its own field, else the one frozen into its argv.

    A Gmail approval draft persisted before the record carried ``cc`` keeps the Cc only
    in the approved argv. Reading an absent field as an empty Cc rewrote ``--cc`` to
    nothing, so the post-approval draft hash no longer matched the action hash the owner
    approved and the send was refused (repair t_0c46c0ad).
    """
    value = draft.get("cc")
    if isinstance(value, str):
        return value
    argv = draft.get("argv")
    if not isinstance(argv, list):
        return ""
    for index, item in enumerate(argv[:-1]):
        following = argv[index + 1]
        if item == "--cc" and isinstance(following, str):
            return following
    return ""


def _payload_text(payload: Mapping[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise MailPreflightError(f"정규화된 메일 {field} 값이 없습니다", 3)
    return value


def _text(draft: Mapping[str, JsonValue], field: str) -> str:
    value = draft.get(field)
    if not isinstance(value, str):
        raise MailPreflightError(f"드래프트 {field} 값이 없습니다", 3)
    return value


def _external_system(draft: Mapping[str, JsonValue]) -> str:
    return "gmail" if draft.get("provider") == "gmail" else "mailon"


def _fingerprint(payload: Mapping[str, str]) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"

"""Construct and parse the hash-bound Gmail approval snapshot."""
from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Mapping
from typing import Final

import mail_gmail_send
import triage_core
from gmail_approval_models import (
    ApprovalSnapshotMismatchError,
    GmailApprovalSnapshot,
    GmailAttachmentSnapshot,
    JsonValue,
)

GMAIL_TOOL_NAME: Final = "gws"
GMAIL_TARGET_ID: Final = "tool:gws_gmail_send:gws"


def build_approval_snapshot(action: mail_gmail_send.CanonicalMailAction) -> GmailApprovalSnapshot:
    """Freeze the Gmail action facts which the existing owner-DM renderer displays."""
    if action.account != "gmail" or action.argv[:2] != ("gws", "gmail"):
        raise ApprovalSnapshotMismatchError()
    action_kind = action.argv[2] if len(action.argv) > 2 else ""
    if action_kind not in {"+send", "+reply"}:
        raise ApprovalSnapshotMismatchError()
    attachments = tuple(
        GmailAttachmentSnapshot(
            source_path_private=entry.source_path_private,
            filename=entry.filename,
            size_bytes=entry.size_bytes,
            mime_type=entry.mime_type,
            sha256=entry.sha256,
        )
        for entry in action.attachment_manifest
    )
    manifest = [attachment.verification_record() for attachment in attachments]
    return GmailApprovalSnapshot(
        sender_account=action.account,
        action_kind=action_kind,
        recipients=_option(action.argv, "--to") or "",
        subject=_option(action.argv, "--subject") or "",
        body=_required_option(action.argv, "--body"),
        reply_target=_option(action.argv, "--message-id"),
        argv=action.argv,
        attachments=attachments,
        attachment_manifest_sha256=(
            triage_core.attachment_manifest_sha256(manifest) if manifest else None
        ),
        action_hash=action_hash(action.argv),
    )


def approval_draft(snapshot: GmailApprovalSnapshot, *, draft_id: str, created_at: str) -> dict[str, JsonValue]:
    """Shape the snapshot for the existing mail approval producer and renderer."""
    attachments = [attachment.verification_record() for attachment in snapshot.attachments]
    record: dict[str, JsonValue] = {
        "approval_action_hash": snapshot.action_hash,
        "argv": list(snapshot.argv),
        "attachments": attachments,
        "attachment_manifest_sha256": snapshot.attachment_manifest_sha256,
        "body": snapshot.body,
        "category": "gmail",
        # The Cc lives in the frozen argv; the draft must ship it as a field too, or the
        # pre-send normalizer reads an absent Cc as an empty one and rewrites ``--cc``
        # away — moving the draft hash off the one the owner approved (t_0c46c0ad).
        "cc": _option(snapshot.argv, "--cc") or "",
        "channel_id": "",
        "created": created_at,
        "flags": [],
        "gmail_approval_snapshot": snapshot.record(),
        "id": draft_id,
        "kind": "compose" if snapshot.action_kind == "+send" else "reply",
        "mail_subject": "",
        "message_id": "",
        "provider": "gmail",
        "reply_target": snapshot.reply_target,
        "sender": snapshot.sender_account,
        "sender_account": snapshot.sender_account,
        "sender_masked": triage_core.mask_value(snapshot.sender_account),
        "status": "pending",
        "subject": snapshot.subject,
        "to": snapshot.recipients,
        "uid": f"gmail:{draft_id}",
        "uid_opaque": triage_core.mask_value(f"gmail:{draft_id}"),
    }
    bcc = _option(snapshot.argv, "--bcc")
    if bcc:
        import mail_bcc

        record["bcc"] = list(mail_bcc._mailboxes(bcc, 1))
    record["sha256"] = triage_core.draft_sha256(record)
    return record


def snapshot_from_draft(draft: Mapping[str, JsonValue]) -> GmailApprovalSnapshot:
    """Parse the stored Gmail snapshot and reject any draft-field divergence."""
    import mail_bcc

    raw_snapshot = draft.get("gmail_approval_snapshot")
    if not isinstance(raw_snapshot, dict):
        raise ApprovalSnapshotMismatchError()
    raw_attachments = raw_snapshot.get("attachments")
    if not isinstance(raw_attachments, list):
        raise ApprovalSnapshotMismatchError()
    attachments = tuple(_attachment_from_record(item) for item in raw_attachments)
    snapshot = GmailApprovalSnapshot(
        sender_account=_string(raw_snapshot, "sender_account"),
        action_kind=_string(raw_snapshot, "action_kind"),
        recipients=_string(raw_snapshot, "recipients"),
        subject=_string(raw_snapshot, "subject"),
        body=_string(raw_snapshot, "body"),
        reply_target=_optional_string(raw_snapshot, "reply_target"),
        argv=_string_tuple(raw_snapshot.get("argv")),
        attachments=attachments,
        attachment_manifest_sha256=_optional_string(raw_snapshot, "attachment_manifest_sha256"),
        action_hash=_string(raw_snapshot, "action_hash"),
    )
    if (
        snapshot.action_hash != action_hash(snapshot.argv)
        or raw_snapshot != snapshot.record()
        or draft.get("approval_action_hash") != snapshot.action_hash
        or draft.get("argv") != list(snapshot.argv)
        or draft.get("attachments") != [item.verification_record() for item in snapshot.attachments]
        or draft.get("attachment_manifest_sha256") != snapshot.attachment_manifest_sha256
        or draft.get("body") != snapshot.body
        or draft.get("provider") != "gmail"
        or draft.get("reply_target") != snapshot.reply_target
        or draft.get("sender_account") != snapshot.sender_account
        or draft.get("subject") != snapshot.subject
        or draft.get("to") != snapshot.recipients
        or ("bcc" in draft and draft["bcc"] != list(
            mail_bcc._mailboxes(_option(snapshot.argv, "--bcc") or "", 1)
        ))
    ):
        raise ApprovalSnapshotMismatchError()
    return snapshot


def _option(argv: tuple[str, ...], name: str) -> str | None:
    indices = tuple(index for index, value in enumerate(argv) if value == name)
    if len(indices) > 1:
        raise ApprovalSnapshotMismatchError()
    if not indices:
        return None
    index = indices[0]
    if index + 1 >= len(argv):
        raise ApprovalSnapshotMismatchError()
    return argv[index + 1]


def _required_option(argv: tuple[str, ...], name: str) -> str:
    value = _option(argv, name)
    if value is None:
        raise ApprovalSnapshotMismatchError()
    return value


def action_hash(argv: tuple[str, ...]) -> str:
    payload = {
        "action": "external_effect.tool_call",
        "arguments": {"command": shlex.join(argv)},
        "target_id": GMAIL_TARGET_ID,
        "tool_name": GMAIL_TOOL_NAME,
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _attachment_from_record(value: JsonValue) -> GmailAttachmentSnapshot:
    if not isinstance(value, dict):
        raise ApprovalSnapshotMismatchError()
    size_bytes = value.get("size_bytes")
    if not isinstance(size_bytes, int):
        raise ApprovalSnapshotMismatchError()
    return GmailAttachmentSnapshot(
        source_path_private=_string(value, "source_path_private"),
        filename=_string(value, "filename"),
        size_bytes=size_bytes,
        mime_type=_string(value, "mime_type"),
        sha256=_string(value, "sha256"),
    )


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise ApprovalSnapshotMismatchError()
    return result


def _optional_string(value: Mapping[str, JsonValue], key: str) -> str | None:
    result = value.get(key)
    if result is None or isinstance(result, str):
        return result
    raise ApprovalSnapshotMismatchError()


def _string_tuple(value: JsonValue | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ApprovalSnapshotMismatchError()
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ApprovalSnapshotMismatchError()
        strings.append(item)
    return tuple(strings)

"""Fail-closed approval contracts for the Gmail outbound execution boundary."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import gmail_approval_gate as gmail_gate  # noqa: E402
import mail_gmail_send  # noqa: E402
import triage_core  # noqa: E402
from automation.interop import effect_approval  # noqa: E402

NOW = datetime(2026, 7, 29, tzinfo=UTC)
OWNER_ID = "owner-1"


def _action(tmp_path: Path, *, body: str = "Approved body.") -> mail_gmail_send.CanonicalMailAction:
    attachment = tmp_path / "notes.txt"
    attachment.write_bytes(b"approved attachment\n")
    return mail_gmail_send.build_action(
        mail_gmail_send.NewMailRequest(
            options=mail_gmail_send.DeliveryOptions(account="gmail", attachments=(attachment,)),
            to="recipient@example.test",
            subject="Approval-gated Gmail send",
            body=body,
        )
    )


def _reply_action(*, reply_target: str) -> mail_gmail_send.CanonicalMailAction:
    return mail_gmail_send.build_action(
        mail_gmail_send.ReplyMailRequest(
            options=mail_gmail_send.DeliveryOptions(account="gmail"),
            reply_to_account="gmail",
            reply_message_id=reply_target,
            body="Approved reply.",
            to="recipient@example.test",
            subject="Re: Approval-gated Gmail send",
        )
    )


def _write_records(path: Path, *records: dict[str, object]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _approved_record(snapshot: gmail_gate.GmailApprovalSnapshot) -> dict[str, object]:
    return gmail_gate.approval_record(
        snapshot,
        gmail_gate.OwnerApproval(
            owner_id=OWNER_ID,
            message_id="masked-message",
            approved_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        ),
    )


def _recording_runner(
    calls: list[tuple[str, ...]], *, expected_environment: Mapping[str, str] | None = None
):
    def run(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert timeout == mail_gmail_send.GWS_SEND_TIMEOUT_SECONDS
        assert check is False
        assert env == (expected_environment or {"PATH": "/offline"})
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="submitted", stderr="")

    return run


def _context(approval_log: Path, calls: list[tuple[str, ...]]) -> gmail_gate.GmailExecutionContext:
    return gmail_gate.GmailExecutionContext(
        approval_log=approval_log,
        owner_id=OWNER_ID,
        now=NOW,
        runner=_recording_runner(calls),
        environment={"PATH": "/offline"},
    )


def test_execution_when_approval_is_missing_then_gws_is_never_invoked(tmp_path: Path) -> None:
    # Given: a canonical Gmail action with no owner approval record
    action = _action(tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(action)
    calls: list[tuple[str, ...]] = []

    # When: execution reaches the approval boundary
    with pytest.raises(gmail_gate.ApprovalRequiredError):
        gmail_gate.execute_approved_gmail(
            snapshot,
            action,
            _context(tmp_path / "approvals.jsonl", calls),
        )

    # Then: the external effect has not started
    assert calls == []


def test_execution_when_owner_approval_matches_then_runs_exact_argv_once(tmp_path: Path) -> None:
    # Given: exactly one still-valid approval record for the frozen action
    action = _action(tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(action)
    approval_log = tmp_path / "approvals.jsonl"
    record = _approved_record(snapshot)
    _write_records(approval_log, record)
    calls: list[tuple[str, ...]] = []

    # When: the approval boundary performs the real send
    result = gmail_gate.execute_approved_gmail(
        snapshot,
        action,
        _context(approval_log, calls),
    )

    # Then: exactly the logged argv and action hash were executed once
    assert result == mail_gmail_send.ExecutionResult(0, "submitted", "")
    assert calls == [snapshot.argv]
    assert tuple(record["argv"]) == snapshot.argv
    assert record["hash"] == snapshot.action_hash
    decision = effect_approval.validate_approval(
        effect_approval.ToolCall(
            tool_name="gws", arguments={"command": shlex.join(snapshot.argv)}
        ),
        gmail_gate.GMAIL_TARGET_ID,
        effect_approval.ApprovalContext(approval_log, OWNER_ID, False),
    )
    assert decision.allowed and decision.action_hash == snapshot.action_hash


def test_execution_when_gws_is_called_then_attachment_verification_is_its_immediate_predecessor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a valid approval and a verifier/runner event recorder
    action = _action(tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(action)
    approval_log = tmp_path / "approvals.jsonl"
    _write_records(approval_log, _approved_record(snapshot))
    events: list[str] = []
    original_verify = triage_core.verify_attachment_manifest

    def record_verify(manifest: list[dict], expected_sha256: str) -> None:
        events.append("verify")
        original_verify(manifest, expected_sha256)

    def record_gws(
        argv: list[str], *, capture_output: bool, text: bool, timeout: int, check: bool, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        _ = (capture_output, text, timeout, check, env)
        events.append("gws")
        return subprocess.CompletedProcess(argv, 0, stdout="submitted", stderr="")

    monkeypatch.setattr(triage_core, "verify_attachment_manifest", record_verify)
    context = gmail_gate.GmailExecutionContext(
        approval_log=approval_log,
        owner_id=OWNER_ID,
        now=NOW,
        runner=record_gws,
        environment={"PATH": "/offline"},
    )

    # When: execution crosses the real subprocess boundary
    gmail_gate.execute_approved_gmail(snapshot, action, context)

    # Then: the existing manifest verifier runs immediately before gws
    assert events == ["verify", "gws"]


def test_execution_when_body_changes_after_approval_then_blocks_without_send(tmp_path: Path) -> None:
    # Given: an approval for a specific message body
    approved_action = _action(tmp_path, body="Approved body.")
    snapshot = gmail_gate.build_approval_snapshot(approved_action)
    approval_log = tmp_path / "approvals.jsonl"
    _write_records(approval_log, _approved_record(snapshot))
    calls: list[tuple[str, ...]] = []

    # When: the body is rebuilt differently after approval
    changed_action = _action(tmp_path, body="Changed body.")
    with pytest.raises(gmail_gate.ApprovalSnapshotMismatchError):
        gmail_gate.execute_approved_gmail(
            snapshot,
            changed_action,
            _context(approval_log, calls),
        )

    # Then: no gws command is eligible to run
    assert calls == []


@pytest.mark.parametrize("mutation", ("swap", "modify", "delete"))
def test_execution_when_attachment_changes_after_approval_then_blocks_without_send(
    tmp_path: Path, mutation: str
) -> None:
    # Given: an approved attachment manifest
    action = _action(tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(action)
    approval_log = tmp_path / "approvals.jsonl"
    _write_records(approval_log, _approved_record(snapshot))
    attachment = Path(snapshot.attachments[0].source_path_private)
    calls: list[tuple[str, ...]] = []

    # When: the approved source is replaced, modified, or removed before gws
    match mutation:
        case "swap":
            attachment.unlink()
            attachment.write_bytes(b"swapped attachment\n")
        case "modify":
            attachment.write_bytes(b"modified attachment\n")
        case "delete":
            attachment.unlink()
        case unreachable:
            raise AssertionError(f"unexpected mutation: {unreachable}")
    with pytest.raises(gmail_gate.ApprovalSnapshotMismatchError):
        gmail_gate.execute_approved_gmail(
            snapshot,
            action,
            _context(approval_log, calls),
        )

    # Then: attachment drift fails closed before the gws call
    assert calls == []


def test_execution_when_recipient_or_reply_target_changes_then_blocks_without_send(tmp_path: Path) -> None:
    # Given: separately approved new-mail and reply targets
    new_action = _action(tmp_path)
    new_snapshot = gmail_gate.build_approval_snapshot(new_action)
    reply_action = _reply_action(reply_target="message-approved")
    reply_snapshot = gmail_gate.build_approval_snapshot(reply_action)
    approval_log = tmp_path / "approvals.jsonl"
    _write_records(approval_log, _approved_record(new_snapshot), _approved_record(reply_snapshot))
    calls: list[tuple[str, ...]] = []
    changed_recipient = mail_gmail_send.build_action(
        mail_gmail_send.NewMailRequest(
            options=mail_gmail_send.DeliveryOptions(account="gmail"),
            to="changed@example.test",
            subject="Approval-gated Gmail send",
            body="Approved body.",
        )
    )

    # When: either the recipient or reply target diverges from its snapshot
    with pytest.raises(gmail_gate.ApprovalSnapshotMismatchError):
        gmail_gate.execute_approved_gmail(
            new_snapshot,
            changed_recipient,
            _context(approval_log, calls),
        )
    with pytest.raises(gmail_gate.ApprovalSnapshotMismatchError):
        gmail_gate.execute_approved_gmail(
            reply_snapshot,
            _reply_action(reply_target="message-changed"),
            _context(approval_log, calls),
        )

    # Then: neither changed target reaches gws
    assert calls == []


@pytest.mark.parametrize("state", ("rejected", "expired", "duplicate"))
def test_execution_when_approval_is_not_singly_valid_then_cannot_bypass(
    tmp_path: Path, state: str
) -> None:
    # Given: a canonical action and an invalid owner-approval state
    action = _action(tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(action)
    record = _approved_record(snapshot)
    match state:
        case "rejected":
            record["result"] = {"status": "rejected"}
            records = (record,)
        case "expired":
            record["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
            records = (record,)
        case "duplicate":
            records = (record, _approved_record(snapshot))
        case unreachable:
            raise AssertionError(f"unexpected state: {unreachable}")
    approval_log = tmp_path / "approvals.jsonl"
    _write_records(approval_log, *records)
    calls: list[tuple[str, ...]] = []

    # When: execution requests authority from the malformed approval state
    with pytest.raises(gmail_gate.ApprovalRequiredError):
        gmail_gate.execute_approved_gmail(
            snapshot,
            action,
            _context(approval_log, calls),
        )

    # Then: no invalid confirmation bypasses the gate
    assert calls == []


def test_execution_when_retried_after_success_then_never_double_sends(tmp_path: Path) -> None:
    # Given: a valid approval and a successful first execution
    action = _action(tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(action)
    approval_log = tmp_path / "approvals.jsonl"
    _write_records(approval_log, _approved_record(snapshot))
    calls: list[tuple[str, ...]] = []
    gmail_gate.execute_approved_gmail(
        snapshot,
        action,
        _context(approval_log, calls),
    )

    # When: the same approval is retried after its send completion was recorded
    with pytest.raises(gmail_gate.ApprovalAlreadyConsumedError):
        gmail_gate.execute_approved_gmail(
            snapshot,
            action,
            _context(approval_log, calls),
        )

    # Then: retry cannot send a second copy
    assert calls == [snapshot.argv]


def test_owner_dm_when_gmail_snapshot_has_attachment_then_triple_metadata_matches(tmp_path: Path) -> None:
    # Given: a canonical Gmail action whose attachment is approval-bound
    action = _action(tmp_path)
    snapshot = gmail_gate.build_approval_snapshot(action)
    draft = gmail_gate.approval_draft(snapshot, draft_id="gmail123", created_at="2026-07-29T00:00:00Z")
    assert gmail_gate.snapshot_from_draft(draft) == snapshot

    # When: the existing mail approval renderer and pre-execution reader observe it
    owner_dm = triage_core.render_approvals_message(
        draft, destination=triage_core.ApprovalRenderDestination.OWNER_DM
    )
    recomputed = gmail_gate.recompute_attachment_manifest(snapshot)

    # Then: filename, byte size, and sha256 agree in DM, stored snapshot, and reread data
    expected = snapshot.attachments[0]
    stored = draft["gmail_approval_snapshot"]["attachments"][0]
    actual = recomputed[0]
    assert (stored["filename"], stored["size_bytes"], stored["sha256"]) == (
        expected.filename,
        expected.size_bytes,
        expected.sha256,
    ) == (actual.filename, actual.size_bytes, actual.sha256)
    for displayed in (expected.filename, str(expected.size_bytes), expected.sha256):
        assert displayed in owner_dm
    for required in (snapshot.sender_account, snapshot.action_kind, snapshot.recipients, snapshot.subject, snapshot.body):
        assert required in owner_dm

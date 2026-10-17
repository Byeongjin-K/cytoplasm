"""Gmail BCC stays in the canonical approved action and private draft."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/mail/scripts"))

import gmail_approval_gate  # noqa: E402
import mail_gmail_send  # noqa: E402
import mail_preflight  # noqa: E402
import triage_core  # noqa: E402
from gmail_approval_models import ApprovalSnapshotMismatchError  # noqa: E402


def _draft():
    action = mail_gmail_send.build_action(mail_gmail_send.NewMailRequest(
        options=mail_gmail_send.DeliveryOptions(account="gmail", bcc="hidden@example.test"),
        to="owner@example.test", subject="seminar", body="notice",
    ))
    snapshot = gmail_approval_gate.build_approval_snapshot(action)
    return gmail_approval_gate.approval_draft(snapshot, draft_id="abc123", created_at="now")


def test_gmail_bcc_action_survives_draft_and_preflight():
    # Given
    draft = _draft()
    # When
    updated = mail_preflight._draft_with_payload(
        draft, {"to": draft["to"], "cc": "", "subject": draft["subject"], "body": draft["body"]},
    )
    # Then
    snapshot = gmail_approval_gate.snapshot_from_draft(updated)
    assert snapshot.argv[snapshot.argv.index("--bcc") + 1] == "hidden@example.test"
    assert updated["bcc"] == ["hidden@example.test"]
    assert "hidden@example.test" not in triage_core.render_approvals_message(updated)


def test_gmail_bcc_record_cannot_diverge_from_approved_argv():
    # Given
    draft = _draft()
    draft["bcc"] = ["changed@example.test"]
    # When / Then
    with pytest.raises(ApprovalSnapshotMismatchError):
        gmail_approval_gate.snapshot_from_draft(draft)

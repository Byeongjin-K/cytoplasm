"""Characterization tests pinning the CURRENT observable contracts of the two
owner-approval gates that already carry a partial duplicate guard: mail triage
drafts (has_draft_for + repost-only-if-blank) and the repair pending store
(same-ticket guard). The budget gate was retired 2026-10-22. Pinned so the upcoming
shared "exactly one live approval message per logical key" module cannot silently
change them; "characterized, not endorsed" marks behavior locked as-is, not approved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
import triage_pipeline  # noqa: E402
from automation.repair.repair_ops_pending import (  # noqa: E402
    APPROVE_EMOJI,
    CANCEL_EMOJI,
    PendingRepairApprovalStore,
    PostingOwnerApproval,
)
from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ApprovalSurface, POLICY_VERSION  # noqa: E402

OWNER_ID = "owner-1"
POSTED_MESSAGE_ID = "m-9"
NOW = datetime(2026, 7, 25, 9, 0, tzinfo=UTC)
REPAIR_BINDING = ApprovalBinding(ApprovalKind.REPAIR, ApprovalSurface.OWNER_DM, "1528936606946122423", POLICY_VERSION)

# The mail draft record gained the resolved approval binding (AS-1.x): `surface`
# and `policy_version` join the `channel_id` it already carried, so a posted draft
# names WHERE its approval message lives and the policy version it was stamped at.
# 2026-08-22: `origin_channel_id`/`origin_message_id` route the send/cancel RESULT
# back to the requesting channel's thread — they are outside the sha256 binding.
MAIL_DRAFT_KEYS = [
    "argv", "body", "category", "cc", "channel_id", "created", "flags", "id", "kind",
    "mail_subject", "message_id", "origin_channel_id", "origin_message_id",
    "policy_version", "sender", "sender_masked",
    "sha256", "status", "subject", "surface", "to", "uid",
    "uid_opaque",
]
# RTS-4 added the four content-binding keys: the record must be able to reproduce
# the approval message, and the message now names the changed files and the digest.
REPAIR_PENDING_KEYS = [
    "action_hash", "changes", "channel_id", "content_binding_version", "content_sha256", "created_at", "kind",
    "message_id", "nonce", "patch_name", "patch_sha256", "patch_source_path", "policy_version",
    "render_version", "surface", "ticket_id",
]


# ---------------------------------------------------------------------- mail

def _mail_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Confine every mail gate path to tmp_path; never ~/.hermes, never a mailbox."""
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "mail-gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail-home"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    return tmp_path / "mail-gate" / "drafts"


def _new_mail_draft(uid: str = "u-1") -> dict:
    return triage_gate.create_draft(
        uid=uid, sender="발신자 <s@example.invalid>", mail_subject="문의",
        to="owner@example.invalid", subject="Re: 문의", body="본문",
        category="important", flags=("reply_needed",),
    )


def _stored(drafts: Path, draft_id: str) -> dict:
    return json.loads((drafts / f"{draft_id}.json").read_text(encoding="utf-8"))


def _run_mail_watch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    posted: list[str] = []

    def post(draft: dict) -> str:
        posted.append(draft["id"])
        return POSTED_MESSAGE_ID

    def refuse(_draft: dict) -> str:
        raise triage_gate.GateError("승인 없음", 1)

    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_pipeline, "_post_draft_for_approval", post)
    monkeypatch.setattr(triage_confirm, "resolve_reaction", refuse)
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    return posted


def test_mail_draft_record_has_exactly_these_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a mail gate rooted entirely under tmp_path
    drafts = _mail_env(tmp_path, monkeypatch)
    # When: the owner-instruction path creates one non-sensitive reply draft
    record = _new_mail_draft()
    # Then: the record carries exactly this field set, byte-identical on disk, with
    # the binding seeded empty — the surface is resolved at intent time, never
    # guessed at creation time
    assert sorted(record) == MAIL_DRAFT_KEYS
    assert _stored(drafts, record["id"]) == record
    assert (record["channel_id"], record["surface"], record["policy_version"]) == ("", None, None)
    assert record["message_id"] == "" and record["status"] == "pending"


def test_mail_set_message_id_merges_and_persists_without_rehashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a freshly created, unposted mail draft
    drafts = _mail_env(tmp_path, monkeypatch)
    record = _new_mail_draft()
    # When: the approval message id is bound to it
    updated = triage_gate.set_message_id(record, "m-1")
    # Then: only message_id changes, returned and stored shapes are identical, and
    # the content hash is NOT recomputed (characterized, not endorsed)
    assert updated == {**record, "message_id": "m-1"}
    assert _stored(drafts, record["id"]) == updated
    assert updated["sha256"] == record["sha256"]


def test_mail_has_draft_for_reports_only_uids_with_a_stored_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an empty mail draft store
    _mail_env(tmp_path, monkeypatch)
    assert triage_gate.has_draft_for("u-1") is False
    # When: one draft exists for u-1
    _new_mail_draft("u-1")
    # Then: only that uid is reported as already drafted
    assert triage_gate.has_draft_for("u-1") is True
    assert triage_gate.has_draft_for("u-2") is False


def test_mail_watch_leaves_a_draft_that_already_has_a_message_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a pending draft already bound to an approval message
    _mail_env(tmp_path, monkeypatch)
    triage_gate.set_message_id(_new_mail_draft(), "m-1")
    # When: the production watch tick runs with no owner reaction
    posted = _run_mail_watch(monkeypatch)
    # Then: nothing is reposted and nothing is printed
    assert posted == []
    assert capsys.readouterr().out == ""


def test_mail_watch_reposts_a_blank_message_id_without_persisting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a pending draft that was never posted
    drafts = _mail_env(tmp_path, monkeypatch)
    record = _new_mail_draft()
    # When: the production watch tick runs with no owner reaction
    posted = _run_mail_watch(monkeypatch)
    # Then: it reposts once, prints exactly this token, but the new message id is
    # kept in memory only — the stored draft stays blank (characterized, not endorsed)
    assert posted == [record["id"]]
    assert capsys.readouterr().out == (
        f"REPOSTED draft={record['id']} message={POSTED_MESSAGE_ID}\n"
    )
    assert _stored(drafts, record["id"])["message_id"] == ""


# -------------------------------------------------------------------- repair

class _FakeDiscord:
    """Offline stand-in for the repair approval transport (no network)."""

    def __init__(self) -> None:
        self.posts: list[str] = []
        self.reactions: list[tuple[str, str]] = []

    def post_approval(self, content: str) -> str:
        self.posts.append(content)
        return f"msg-{len(self.posts)}"

    def add_reaction(self, message_id: str, emoji: str) -> None:
        self.reactions.append((message_id, emoji))


def _posting(tmp_path: Path, discord: _FakeDiscord) -> PostingOwnerApproval:
    store = PendingRepairApprovalStore(tmp_path / "pending")
    return PostingOwnerApproval(OWNER_ID, store, discord, now=lambda: NOW, nonce=lambda: "a" * 32, binding=REPAIR_BINDING)


def _repair_patch(tmp_path: Path, replacement: str = "new") -> Path:
    """The approval binds patch BYTES (RTS-4), so the file has to be real."""
    target = tmp_path / "plans" / "t-1" / "patch.diff"
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(
        "diff --git a/automation/mod.py b/automation/mod.py\n"
        "--- a/automation/mod.py\n"
        "+++ b/automation/mod.py\n"
        f"@@ -1,2 +1,2 @@\n context\n-old\n+{replacement}\n",
        encoding="utf-8",
    )
    return target


def test_repair_pending_record_field_set_and_filename_derivation(tmp_path: Path) -> None:
    # Given: an empty ops-private pending root and a green sandbox result
    discord = _FakeDiscord()
    approval = _posting(tmp_path, discord)
    # When: the repair gate records one owner request
    # (permits ALWAYS returns False — mutation is deferred to a later poll)
    assert approval.permits("t-1", _repair_patch(tmp_path)) is False
    # Then: the file is named by sha256 of the ticket id and carries exactly these fields
    payload = json.loads((tmp_path / "pending" / f"{hashlib.sha256(b't-1').hexdigest()}.json").read_text(encoding="utf-8"))
    assert sorted(payload) == REPAIR_PENDING_KEYS
    assert payload["message_id"] == "msg-1"
    assert payload["created_at"] == NOW.isoformat()
    assert (payload["kind"], payload["surface"], payload["channel_id"], payload["policy_version"]) == (ApprovalKind.REPAIR.value, ApprovalSurface.OWNER_DM.value, REPAIR_BINDING.channel_id, POLICY_VERSION)
    assert discord.reactions == [("msg-1", APPROVE_EMOJI), ("msg-1", CANCEL_EMOJI)]


def test_repair_permits_posts_nothing_when_the_ticket_already_has_a_request(
    tmp_path: Path,
) -> None:
    # Given: one already-posted pending request for this ticket
    discord = _FakeDiscord()
    approval = _posting(tmp_path, discord)
    assert approval.permits("t-1", _repair_patch(tmp_path)) is False
    before = approval.store.get("t-1")
    # When: the same ticket reaches the approval boundary again with a new patch
    permitted = approval.permits("t-1", _repair_patch(tmp_path, "a different line"))
    # Then: it refuses without posting, re-reacting, or mutating stored state
    assert permitted is False
    assert len(discord.posts) == 1
    assert discord.reactions == [("msg-1", APPROVE_EMOJI), ("msg-1", CANCEL_EMOJI)]
    assert approval.store.get("t-1") == before

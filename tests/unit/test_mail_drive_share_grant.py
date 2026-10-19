"""Drive share mail after the 2026-10-19 girder sends: invites, reuse, failure detail, card v5.

Two owner-approved sends stopped in the Drive step. The upload had finished; Drive then
refused a silent view-only invite to an address with no Google account, and the record
only said ``drive_upload_failed``. The owner chose (Q273) to share anyway with Google's own
invite mail. These tests sit beside ``test_mail_drive_share.py`` and reuse its fake Drive,
mailon and compose CLI so the owner-facing behavior is one story.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from automation import drive_outputs
from automation.drive_client import DriveClient, DriveClientError
from tests.unit import test_mail_drive_share as base
from tests.unit.test_mail_drive_share import APPROVAL, PAYLOAD, PERSON, FakeDrive, _compose, _package, _record

import mail_approval_attachment  # noqa: E402
import mail_card_compact  # noqa: E402
import mail_send_retry  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402

DRIVE_REFUSAL = (
    "gws drive permissions create 실패 rc=1: error[api]: Bad Request. User message: "
    "\"viewer.person@example.com님을 초대하려고 합니다. 이 이메일 계정에 연결된 Google 계정이 없기 때문에 "
    "이 수신자를 초대하려면 '이메일 알림 보내기'를 선택해야 합니다.\""
)


env = base.env
sends = base.sends


class InviteDrive(FakeDrive):
    """Drive that refuses a silent invite to anyone outside ``google_accounts``."""

    def __init__(self) -> None:
        super().__init__()
        self.google_accounts: set[str] = set()
        self.refuse_every_invite = False

    def __call__(self, argv: list[str]) -> dict[str, object]:
        if argv[2:4] == ["permissions", "create"]:
            notify = json.loads(argv[argv.index("--params") + 1])["sendNotificationEmail"]
            email = json.loads(argv[argv.index("--json") + 1])["emailAddress"]
            if self.refuse_every_invite or (not notify and email not in self.google_accounts):
                self.calls.append(argv)
                raise DriveClientError(DRIVE_REFUSAL)
        return super().__call__(argv)

    def invites(self) -> list[bool]:
        return [json.loads(call[call.index("--params") + 1])["sendNotificationEmail"]
                for call in self.calls if call[2:4] == ["permissions", "create"]]


@pytest.fixture
def drive(env: Path, monkeypatch: pytest.MonkeyPatch) -> InviteDrive:
    fake = InviteDrive()
    client = DriveClient(gws_bin="gws", folder_cache=env / "folders.json", runner=fake)
    monkeypatch.setattr(drive_outputs, "client_from_environment", lambda: client)
    return fake


def test_an_address_without_a_google_account_is_shared_with_googles_invite_and_the_mail_goes(
    env: Path, drive: InviteDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an approved share mail to someone Drive will not invite silently.
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    # When: the watcher executes it.
    triage_gate.execute_draft(_record(), APPROVAL)
    # Then: the silent invite is refused, Google's invite is used, and the mail carries the link.
    assert drive.invites() == [False, True]
    [argv] = sends
    assert "https://drive.google.com/file/d/" in argv[list(argv).index("--body") + 1]
    stored = _record()
    assert stored["status"] == "executed"
    assert stored["drive_share_result"]["invited_by_google"] == [PERSON.lower()]


def test_a_google_account_still_gets_only_the_silent_share(
    env: Path, drive: InviteDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the grantee has a Google account.
    drive.google_accounts.add(PERSON.lower())
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    # When: the watcher executes it.
    triage_gate.execute_draft(_record(), APPROVAL)
    # Then: one silent grant, no Google mail, and the mail goes.
    assert drive.invites() == [False] and len(sends) == 1
    assert _record()["drive_share_result"]["invited_by_google"] == []


def test_a_refused_invite_sends_nothing_names_its_cause_and_is_not_retried(
    env: Path, drive: InviteDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Drive refuses the invite even with Google's notification.
    drive.refuse_every_invite = True
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    # When: the watcher executes it.
    with pytest.raises(triage_gate.GateError, match="cause=drive_grant_refused retryable=false"):
        triage_gate.execute_draft(_record(), APPROVAL)
    # Then: no mail left, the private record keeps Drive's own words, and no retry is scheduled.
    stored = _record()
    assert sends == [] and drive.uploads() == 1
    assert stored["status"] == "blocked" and "next_attempt_at" not in stored
    assert "Google 계정이 없기 때문에" in stored["last_error"]["detail"]
    notice = mail_send_retry.failure_text(stored, "x")
    assert "원인: drive_grant_refused" in notice and "Google 계정" not in notice


def test_an_upload_failure_keeps_the_underlying_error_in_the_private_record(
    env: Path, drive: InviteDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Drive rejects the upload itself.
    drive.fail_upload = True
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    # When / Then: the retryable failure says which call failed, not just a category.
    with pytest.raises(triage_gate.GateError, match="cause=drive_upload_failed retryable=true"):
        triage_gate.execute_draft(_record(), APPROVAL)
    assert _record()["last_error"]["detail"].startswith("DriveClientError: drive +upload 실패")


def test_a_second_draft_reuses_the_file_already_in_the_folder_instead_of_uploading_again(
    env: Path, drive: InviteDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one share mail already sent, which uploaded the file once.
    drive.google_accounts.add(PERSON.lower())
    package = _package(env)
    assert _compose(monkeypatch, "--drive-share", str(package), "--share-with", PERSON) == 0
    first = _record()
    triage_gate.execute_draft(first, APPROVAL)
    assert drive.uploads() == 1
    # When: a new draft for the same bytes runs with no receipt of that upload.
    triage_gate._draft_path(first["id"]).unlink()
    assert _compose(monkeypatch, "--drive-share", str(package), "--share-with", PERSON) == 0
    triage_gate.execute_draft(_record(), APPROVAL)
    # Then: Drive still holds one upload and the second mail carries the same file's link.
    assert drive.uploads() == 1 and len(sends) == 2
    assert _record()["drive_share_result"]["action"] == "reused"


def test_card_v5_adds_attachments_and_googles_invite_to_the_share_line(
    env: Path, drive: InviteDrive, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a share mail with one attached photo.
    photo = env / "outbox" / "1-수정후.png"
    photo.parent.mkdir(exist_ok=True)
    photo.write_bytes(b"\x89PNG" + bytes(100))
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON,
                    "--attachment", str(photo)) == 0
    record = _record()
    share = mail_card_compact.share_line(record["drive_share"])
    invite = mail_card_compact.INVITE_NOTICE
    # When: the card is rendered short (inline body) and long (body in the text file).
    long_v4 = mail_approval_attachment.summary({**record, "render_version": "4"}, "✅ / ⛔")
    long_v5 = mail_approval_attachment.summary({**record, "render_version": "5"}, "✅ / ⛔")
    short_v4 = triage_core.render_approvals_message({**record, "render_version": "4"}, instruction="✅ / ⛔")
    short_v5 = triage_core.render_approvals_message({**record, "render_version": "5"}, instruction="✅ / ⛔")
    # Then: v4 replays without them, and v5 shows the photo, who gets the file, and Google's invite.
    assert invite not in long_v4 + short_v4 and "1-수정후.png" not in long_v4
    assert f"{share}\n{invite}" in short_v5 and f"{share}\n{invite}" in long_v5
    assert "- 첨부: 1개\n  - `1-수정후.png` · 104 bytes" in long_v5
    for card in (long_v5, short_v5):
        assert card.endswith("✅ / ⛔") and not re.search(r"[0-9a-f]{64}", card)
        assert hashlib.sha256(PAYLOAD).hexdigest() not in card

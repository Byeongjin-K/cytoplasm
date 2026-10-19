"""Versioned long-mail summaries through the existing loopback attachment surface."""
from __future__ import annotations

from typing import TypeAlias

import pytest

from automation.entity_preflight.contracts import JsonValue
from tests.unit import test_mail_single_live_request as mail
from tests.unit.test_mail_approval_message_attachment import (
    attachment_wire as attachment_wire, long_draft,
)
from tests.unit.test_mail_single_live_request import mail_env as mail_env
from skills.mail.scripts import mail_approval_attachment
from automation.interop import owner_message
from automation.interop.approval_card import CardRenderError, prepare
from tests.unit.mail_approval_card_golden import MAIL_ATTACHMENT_V3

AttachmentWire: TypeAlias = tuple[
    mail.FakeDiscord, list[tuple[dict[str, JsonValue], str, bytes]],
    dict[str, JsonValue], dict[str, bytes],
]


def test_summary_v3_when_new_long_mail_is_posted(attachment_wire: AttachmentWire) -> None:
    # Given a long body requiring the existing attachment representation.
    fake, uploads = attachment_wire[:2]
    draft: dict[str, JsonValue] = long_draft()
    # When the real producer uploads through loopback HTTP.
    message_id = mail.triage_approval.post_for_approval(draft)
    # Then the new presentation and original attachment contract coexist.
    stored = mail.triage_gate.load_draft(draft["id"])
    assert stored["render_version"] == "5"
    assert stored["approval_format"] == "attachment-v1"
    assert fake.contents[message_id].startswith("📨 **메일 발송 승인**")
    assert str(draft["body"]) not in fake.contents[message_id]
    assert uploads[0][2] == (str(draft["body"]) + "\n\n" + str(draft["quote"])).encode()
    assert mail.triage_approval.MailApprovalGate(stored).probe(
        mail.triage_approval.request_of(stored),
    ) is mail.triage_approval.lifecycle().Probe.BOUND_PENDING


@pytest.mark.parametrize("version", ["1", "2", "3"])
def test_summary_replay_when_stored_card_is_legacy(version: str) -> None:
    # Given a fixed pre-upgrade summary record.
    draft = {
        "id": "abc123", "sha256": "digest-1", "subject": "S42",
        "to": "to@example.invalid", "cc": "", "body": "B42",
        "render_version": version,
    }
    # When
    content = mail_approval_attachment.summary(draft, "A42")
    # Then this literal is the posted-copy equality contract, not a prose assertion.
    if version == "3":
        assert content == MAIL_ATTACHMENT_V3
        return
    assert content == (
        "[mail-triage] 발송 승인 요청 (SUMMARY)\n"
        "- 제목: S42\n- To: to@example.invalid\n- Cc: -\n"
        "- draft: `abc123` sha256: `digest-1`\n"
        "- body sha256: `72294ba1616ddcaee48e56501040e876d26bd0c1839f3cddd34900f590f717a4`\n"
        "- action hash: `digest-1`\n본문: 첨부 파일 참조\n- 반응(기본): A42"
    )


def test_stored_summary_when_version_is_unknown_refuses() -> None:
    # Given a stored format with an unsupported rendering version.
    draft = {"render_version": "future"}
    # When / Then no legacy card may be substituted.
    with pytest.raises(CardRenderError):
        _ = mail_approval_attachment.summary(draft, "A42")


def test_new_summary_when_optional_renderer_is_missing_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given the same supported old runtime shape as inline cards.
    monkeypatch.delattr(owner_message, "render")
    draft = {
        "id": "abc123", "sha256": "digest-1", "subject": "S42",
        "to": "to@example.invalid", "body": "B42",
    }
    # When
    card = prepare(lambda version: mail_approval_attachment.summary(
        {**draft, "render_version": version}, "A42",
    ))
    # Then capability fallback records the exact legacy version.
    assert card.render_version == "1"
    assert card.content == mail_approval_attachment.summary(draft, "A42")

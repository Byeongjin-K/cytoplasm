"""Default-off and owner-only format test, separate from FS3-pinned regressions."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills/mail/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mail_drive_share  # noqa: E402
import mail_preflight  # noqa: E402
import mail_quote  # noqa: E402
import triage_cli  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
from test_mail_reply_quote import (  # noqa: E402
    OWNER, REPLY_TEXT, _detail, _only_draft, _run_cli, _setup_env,
)


@pytest.fixture
def mail_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path, body_html="<b>원문 서식</b>"))
    monkeypatch.setenv("MAILON_ID", OWNER)
    return home


def _compose(monkeypatch, *extra):
    return _run_cli(monkeypatch, "compose", "--to", OWNER, "--subject", "서식 시험",
                    "--body", "확인 부탁드립니다.", "--no-post", *extra)


def test_absent_switch_keeps_exact_main_plain_reply_argv(mail_env, monkeypatch):
    # Given: no marker and no explicit test request.
    # When
    assert _run_cli(monkeypatch, "draft", "--uid", "u-1", "--instruction", "참석", "--no-post") == 0
    record = _only_draft()[1]
    # Then: byte-identical argv to the pre-HTML plain path, including its original quote.
    expected = triage_core.build_send_argv(
        "python3", record["to"], record["subject"], mail_quote.with_quote(REPLY_TEXT, record["quote"]),
    )
    assert record["argv"] == list(expected)
    assert "--body-html" not in record["argv"]
    assert "quote_html" not in record and "quote_format" not in record
    assert "reply_format" not in record


@pytest.mark.parametrize("extra", [
    (), ("--to", f"형 <{OWNER.upper()}>"), ("--cc", OWNER), ("--bcc", OWNER),
    ("--cc", OWNER, "--bcc", OWNER),
])
def test_explicit_test_accepts_only_owner_mailboxes(mail_env, monkeypatch, extra):
    # Given: all visible and hidden recipients refer to the configured owner.
    # When
    assert _compose(monkeypatch, "--format-test", "html", *extra) == 0
    record = _only_draft()[1]
    # Then
    assert record["reply_format"] == "html-test"
    assert "--body-html" in record["argv"]
    assert "맑은 고딕" in record["argv"][record["argv"].index("--body-html") + 1]


@pytest.mark.parametrize("extra", [
    ("--to", "other@example.invalid"), ("--cc", "other@example.invalid"),
    ("--bcc", "other@example.invalid"), ("--to", f"{OWNER},other@example.invalid"),
    ("--cc", f"{OWNER},other@example.invalid"), ("--bcc", f"{OWNER},other@example.invalid"),
    ("--to", f"{OWNER},broken"), ("--to", f"{OWNER} <other@example.invalid>"),
])
def test_test_format_refuses_any_other_or_unparseable_recipient(mail_env, monkeypatch, capsys, extra):
    # Given: at least one recipient is not the owner's mailbox.
    # When
    assert _compose(monkeypatch, "--format-test", "html", *extra) == 2
    # Then: refuse before persisting a draft or posting a card.
    assert "형 기관메일 주소만" in capsys.readouterr().err
    assert triage_gate.list_drafts() == []


def test_test_format_requires_known_owner_address(mail_env, monkeypatch, capsys):
    # Given: neither MAILON_ID nor the existing secrets fallback can identify the owner.
    monkeypatch.delenv("MAILON_ID")
    # When
    assert _compose(monkeypatch, "--format-test", "html") == 2
    # Then
    assert "형 기관메일 주소만" in capsys.readouterr().err
    assert triage_gate.list_drafts() == []


@pytest.mark.parametrize("sender,code", [(OWNER, 0), ("other@example.invalid", 2)])
def test_draft_command_applies_the_same_owner_only_test_boundary(
    mail_env, monkeypatch, tmp_path, sender, code,
):
    # Given: draft derives recipients from the original sender, never from the model.
    monkeypatch.setattr(triage_cli, "_get_mail", lambda _uid: _detail(tmp_path, sender=sender))
    # When
    assert _run_cli(monkeypatch, "draft", "--uid", "u-1", "--instruction", "시험",
                    "--format-test", "html", "--no-post") == code
    # Then
    drafts = triage_gate.list_drafts()
    assert len(drafts) == (1 if code == 0 else 0)
    if drafts:
        assert drafts[0]["reply_format"] == "html-test"


def test_format_choice_is_bound_independently_of_argv(mail_env, monkeypatch):
    # Given
    assert _compose(monkeypatch) == 0
    plain = _only_draft()[1]
    # When: change only the explicit test designation.
    altered = {**plain, "reply_format": "html-test"}
    # Then: even equal argv cannot reuse a plain approval for the test exception.
    assert triage_core.draft_sha256(altered) != plain["sha256"]


@pytest.mark.parametrize("long_body", [False, True])
def test_card_discloses_owner_only_test_even_with_body_attachment(mail_env, monkeypatch, long_body):
    # Given
    assert _compose(monkeypatch, "--format-test", "html", "--body", "본문" * (2000 if long_body else 1)) == 0
    record = _only_draft()[1]
    import triage_approval
    import triage_approval_gate

    # Keep the actual inline/attachment preparation; stub only the binding/transport boundary.
    monkeypatch.setattr(triage_approval, "confirm_intent", lambda _draft: None)
    monkeypatch.setattr(triage_approval, "reaction_instruction", lambda _draft: "✅ / ⛔")
    # When: prepare the actual card, including automatic attachment selection for long bodies.
    _intent, card = triage_approval_gate.prepare_request(record, "")
    content = card.content
    # Then: compare the shipped field, not an independent prose expectation.
    from mail_reply_format import TEST_CARD_LINE

    assert TEST_CARD_LINE in content
    assert record["sha256"] == triage_core.draft_sha256(record)
    assert card.draft["render_version"] == "6"
    assert ("approval_attachment" in card.draft) is long_body


def test_node_marker_enables_ordinary_draft_and_choice_survives_marker_removal(mail_env, monkeypatch):
    # Given: a released node setting, not an unapproved host flag.
    marker = mail_env / ".hermes/mail-html-reply.mode"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"on")
    assert _compose(monkeypatch, "--to", "other@example.invalid") == 0
    record = _only_draft()[1]
    marker.unlink()
    # When: preflight rebuilds the frozen draft after the switch changes.
    updated = mail_preflight._draft_with_payload(record, {
        "to": record["to"], "cc": "", "subject": record["subject"], "body": "수정",
    })
    # Then: the reviewed format is not reselected at execution time.
    assert record["reply_format"] == updated["reply_format"] == "html"
    assert "--body-html" in updated["argv"]
    assert "수정" in updated["argv"][updated["argv"].index("--body-html") + 1]


def test_test_preflight_cannot_expand_recipients(mail_env, monkeypatch):
    # Given
    assert _compose(monkeypatch, "--format-test", "html") == 0
    record = _only_draft()[1]
    # When / Then
    with pytest.raises(triage_gate.GateError, match="형 기관메일 주소만"):
        mail_preflight._draft_with_payload(record, {
            "to": "other@example.invalid", "cc": "", "subject": record["subject"], "body": record["body"],
        })


def test_test_html_keeps_quote_preflight_and_drive_link_placeholder(mail_env, monkeypatch):
    # Given: an owner-only reply and a Drive placeholder, with no upload or send.
    body = f"자료: {mail_drive_share.PLACEHOLDER}"
    draft = triage_gate.create_draft(
        uid="test", sender="", mail_subject="", to=OWNER, subject="시험", body=body,
        category="compose", flags=(), quote="원문",
        quote_html=mail_quote.QuoteHtml("<b>원문</b>", keeps_format=True), format_test="html",
    )
    # When: the existing preflight and placeholder-filling boundaries run.
    updated = mail_preflight._draft_with_payload(draft, {
        "to": OWNER, "cc": "", "subject": "시험", "body": body,
    })
    argv = mail_drive_share.with_link(updated, "https://drive.invalid/f?id=1&x=2")["send_argv"]
    # Then: both frozen representations retain the original; only placeholders are filled.
    assert updated["reply_format"] == "html-test"
    assert updated["sha256"] == draft["sha256"]
    assert "원문" in argv[argv.index("--body") + 1]
    assert "<b>원문</b>" in argv[argv.index("--body-html") + 1]
    assert mail_drive_share.PLACEHOLDER not in argv[argv.index("--body") + 1]
    assert "&amp;x=2" in argv[argv.index("--body-html") + 1]

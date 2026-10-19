"""A forward card shows no empty body block and no reply-only recipient warning (live QA 1, 2026-10-19).

New file so the recorded forward cases in test_mail_forward.py keep their shape.
"""
from __future__ import annotations

import pytest

import json

from tests.unit.test_mail_forward import (  # noqa: F401  (also puts the mail scripts on sys.path)
    TO, UID, _cli, _drafts, _runtime, env, triage_core, triage_gate,
)

import mail_card_compact  # noqa: E402

GAP = "직전 관련 메일 수신자 중 제외됨"


def test_a_forward_without_a_note_shows_no_empty_body_block(env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    # Given: a forward with no note of its own.
    _runtime(env)
    assert _cli(monkeypatch, "compose", "--forward", UID, "--to", TO, "--no-post") == 0
    [record] = _drafts()
    # When: the current card is rendered.
    card = triage_core.render_approvals_message({**record, "render_version": mail_card_compact.VERSION},
                                                instruction="I")
    # Then: no empty code block, the forward line and attachments stay.
    assert "- 본문:" not in card and "```\n\n```" not in card
    assert "원 첨부 2개가 바이트 그대로" in card and card.endswith("I")


def test_a_forward_note_keeps_its_body_block(env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    # Given: a forward with a note.
    _runtime(env)
    assert _cli(monkeypatch, "compose", "--forward", UID, "--to", TO, "--body", "참고해 주세요", "--no-post") == 0
    [record] = _drafts()
    # When / Then: the note is still shown.
    card = triage_core.render_approvals_message({**record, "render_version": mail_card_compact.VERSION},
                                                instruction="I")
    assert "- 본문:" in card and "참고해 주세요" in card


def test_a_forward_gets_no_reply_recipient_warning_but_a_reply_still_does(
    env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],  # noqa: F811
) -> None:
    # Given: an earlier sent mail of the same thread to two people.
    _runtime(env)
    assert _cli(monkeypatch, "compose", "--to", "a@example.invalid, b@example.invalid",
                "--subject", "Re: 과제 협약서 송부", "--body", "첫 회신", "--no-post") == 0
    [sent] = _drafts()
    path = triage_gate._public_drafts_dir() / f"{sent['id']}.json"
    path.write_text(json.dumps({**sent, "status": "executed"}), encoding="utf-8")
    capsys.readouterr()
    # When: the same thread is forwarded to someone else.
    assert _cli(monkeypatch, "compose", "--forward", UID, "--to", TO, "--no-post") == 0
    forward_out = capsys.readouterr().out
    # And: a new reply leaves one of the earlier recipients out.
    assert _cli(monkeypatch, "compose", "--to", "a@example.invalid", "--subject", "Re: 과제 협약서 송부",
                "--body", "다시 회신", "--no-post") == 0
    reply_out = capsys.readouterr().out
    # Then: only the reply warns.
    assert GAP not in forward_out
    assert GAP in reply_out

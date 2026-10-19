"""A mail card with a bound Drive share says what is shared with whom; a stray placeholder is refused.

Owner 2026-10-19 12:15 KST "앞으로 이런 실수 없도록 해": the short card (and the long-body summary
card, which dropped the share entirely) did not show the share, and a body could carry the link
placeholder with no share bound until the send failed. New file so the recorded drive-share and
v4 card cases keep their shape.
"""
from __future__ import annotations

from tests.unit.test_mail_drive_share import (  # noqa: F401  (also puts the mail scripts on sys.path)
    PERSON, _compose, _package, _record, drive, env, sends, triage_core, triage_gate,
)

import mail_approval_attachment  # noqa: E402
import mail_card_compact  # noqa: E402

LINE = ("- 보낼 때 `Planner-수정본.zip`(10.0 KB) 공유 링크가 채워짐"
        "(공유 대상: `viewer.person@example.com`, 보기)")


def _shared(env, monkeypatch) -> dict:  # noqa: F811
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    return _record()


def test_a_v5_card_shows_one_share_line_without_internals(env, drive, sends, monkeypatch) -> None:  # noqa: F811
    record = {**_shared(env, monkeypatch), "render_version": "5"}
    card = triage_core.render_approvals_message(record, instruction="I")
    assert LINE in card and record["drive_share"]["sha256"] not in card and "올릴 곳" not in card
    assert mail_card_compact.VERSION == "5"


def test_a_stored_v4_card_replays_unchanged(env, drive, sends, monkeypatch) -> None:  # noqa: F811
    card = triage_core.render_approvals_message({**_shared(env, monkeypatch), "render_version": "4"}, instruction="I")
    assert "- Drive 공유: `Planner-수정본.zip` · 10244 bytes" in card and LINE not in card


def test_the_long_body_summary_card_shows_the_share(env, drive, sends, monkeypatch) -> None:  # noqa: F811
    record = {**_shared(env, monkeypatch), "render_version": "5"}
    assert LINE in mail_approval_attachment.summary(record, "I")


def test_a_link_placeholder_without_a_share_is_refused_before_any_draft(env, monkeypatch) -> None:  # noqa: F811
    assert _compose(monkeypatch) != 0
    assert list(triage_gate._public_drafts_dir().glob("*.json")) == []

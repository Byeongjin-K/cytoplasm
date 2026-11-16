"""2026-11-16: full text goes to the mail's own thread; only a link line replies to its card.

Q474 (2026-11-03) had put the parts as replies under the card in the shared digest thread;
the owner then asked "다이제스트에서 전문 확인 및 초안 작성 누르면 별도 스레드로 진행되게 해".
"""
from __future__ import annotations

from tests.unit import test_mail_digest_reactions as digest
from tests.unit.test_mail_digest_reactions import db as db


def test_full_text_parts_go_to_the_mail_thread_and_the_card_gets_one_link(db, monkeypatch):
    digest._discord(monkeypatch, {})
    monkeypatch.setattr(digest.triage_transport, "_get_mail", digest._mail)
    posted = []
    monkeypatch.setattr(
        digest.triage_confirm, "post_in",
        lambda channel, body, reply_to="": posted.append((channel, reply_to)) or "reply",
    )
    assert digest.actions._show_body({
        "uid": "u-1", "subject": "sample", "channel_id": "thread-1", "message_id": "card-1",
    }) == "done"
    assert posted == [("thread-1", "card-1"), (digest.BODY_THREAD, ""), (digest.BODY_THREAD, "")]
    assert len(digest.OPENED) == 1

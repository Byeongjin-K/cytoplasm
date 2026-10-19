"""The first draft of an owner thread is replaced like any later one.

2026-10-19: draft af9375 stayed live after 3b82d6 replaced it because it was written from the
parent-channel message that opened the thread, so its origin channel was not the thread. New file
so the v4 supersede tests keep their recorded shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))

import triage_supersede  # noqa: E402

THREAD = "100000000000000777"
PARENT = "100000000000000111"


def _draft(draft_id: str, origin_channel: str, origin_message: str) -> dict:
    return {"id": draft_id, "kind": "compose", "status": "pending", "approval_thread_id": THREAD,
            "origin_channel_id": origin_channel, "origin_message_id": origin_message}


def test_the_threads_first_draft_is_the_same_request_as_a_later_one() -> None:
    first = _draft("af9375", PARENT, THREAD)
    newer = _draft("3b82d6", THREAD, "100000000000000999")
    assert triage_supersede._same_request(first, newer)


def test_a_draft_from_another_parent_message_is_not_the_same_request() -> None:
    other = _draft("aaaaaa", PARENT, "100000000000000555")
    newer = _draft("3b82d6", THREAD, "100000000000000999")
    assert not triage_supersede._same_request(other, newer)


def test_a_draft_in_another_thread_is_not_the_same_request() -> None:
    elsewhere = {**_draft("bbbbbb", PARENT, THREAD), "approval_thread_id": "100000000000000888"}
    newer = _draft("3b82d6", THREAD, "100000000000000999")
    assert not triage_supersede._same_request(elsewhere, newer)

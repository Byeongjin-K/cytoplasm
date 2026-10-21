"""승인 스레드 좌표 한 줄(`APPROVAL-THREAD`)의 단일 정의를 고정한다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.interop.thread_pointer import approval_thread_line  # noqa: E402

GUILD = "300000000000000001"
THREAD = "300000000000000002"


def test_line_links_the_request_thread_when_both_coordinates_are_known() -> None:
    # Given: a record bound to a guild thread
    record = {"approval_guild_id": GUILD, "approval_thread_id": THREAD}
    # When / Then: the line names the key and the exact thread URL
    assert approval_thread_line("draft", "abc123", record) == (
        f"APPROVAL-THREAD draft=abc123 url=[승인스레드](<https://discord.com/channels/{GUILD}/{THREAD}>)"
    )


def test_line_never_guesses_a_link_without_the_guild() -> None:
    # Given: a legacy binding whose guild was never stored
    record = {"approval_thread_id": THREAD}
    # When / Then: no URL — the value becomes the search key
    assert approval_thread_line("hash", "sha256:ab", record) == (
        "APPROVAL-THREAD hash=sha256:ab url=unavailable search=sha256:ab"
    )


def test_line_rejects_malformed_coordinates_instead_of_rendering_them() -> None:
    # Given: coordinates that are not Discord snowflakes (empty guild, non-numeric thread)
    record = {"approval_guild_id": "", "approval_thread_id": "thread-x"}
    # When / Then: unavailable, never a broken URL
    assert approval_thread_line("draft", "d1", record).endswith("url=unavailable search=d1")

"""Bounded daily-summary behavior in a new file, separate from historical case pins."""
from __future__ import annotations

from datetime import datetime

from automation import auto_write


def test_daily_post_is_bounded_counts_all_kinds_and_hides_links(tmp_path):
    # Given: many receipts including origin links in their private evidence fields.
    for index in range(21):
        kind = tuple(auto_write.KINDS)[index % 7]
        auto_write.record(kind, auto_write.Entry(
            str(index), f"제목 {index}\nhttps://discord.com/channels/1/2/3",
            "https://discord.com/channels/1/2/3", "2026-10-18T09:00:00+09:00",
            f"2026-10-18T10:{index:02}:00+09:00",
        ), root=tmp_path)
    sent = []
    # When: the scheduled summary runs.
    assert auto_write.summarize(datetime.fromisoformat("2026-10-18T21:30:00+09:00"),
                                root=tmp_path, send=lambda text: sent.append(text) or True)
    # Then: one short post counts every kind, with at most five title lines.
    lines = sent[0].splitlines()
    assert len(lines) <= 8
    assert "2026-10-18" in lines[0] and "21" in lines[0]
    assert all(f"{label} 3" in lines[1] for label in auto_write.KINDS.values())
    assert "discord.com" not in sent[0]
    assert "https://" in (tmp_path / "ledger.jsonl").read_text()


def test_empty_day_is_silent_and_failed_send_does_not_advance(tmp_path):
    # Given: no receipts.
    now = datetime.fromisoformat("2026-10-18T21:30:00+09:00")
    sent = []
    assert not auto_write.summarize(now, root=tmp_path, send=lambda text: sent.append(text) or True)
    assert sent == []
    auto_write.record("todo", auto_write.Entry("id", "제목", "rev", now.isoformat(), now.isoformat()),
                      root=tmp_path)
    # When: delivery fails.
    assert not auto_write.summarize(now, root=tmp_path, send=lambda text: False)
    # Then: the watermark remains absent and the next send can deliver it.
    assert not (tmp_path / "summary.json").exists()
    assert auto_write.summarize(now, root=tmp_path, send=lambda text: sent.append(text) or True)
    assert len(sent) == 1

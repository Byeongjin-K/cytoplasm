"""Shared auto-write policy and daily reporting regressions (Q231/Q233)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from automation import auto_write


def _entry(at: str = "2026-10-18T10:00:00+09:00") -> auto_write.Entry:
    return auto_write.Entry("rec-1", "Lifelog/회의.md", "a" * 40,
                            "2026-10-18T09:00:00+09:00", at)


def test_record_is_private_idempotent_and_has_no_body(tmp_path: Path) -> None:
    # Given: a verified automatic write.
    entry = _entry()
    # When: a retried tick records the same receipt.
    auto_write.record("plaud", entry, root=tmp_path)
    auto_write.record("plaud", entry, root=tmp_path)
    # Then: only metadata is retained once, privately.
    ledger = tmp_path / "ledger.jsonl"
    rows = ledger.read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0]) == {"kind": "plaud", **entry.as_dict()}
    assert ledger.stat().st_mode & 0o777 == 0o600


def test_summary_once_daily_and_carries_late_writes(tmp_path: Path) -> None:
    # Given: a day's write and a recording sender.
    auto_write.record("plaud", _entry(), root=tmp_path)
    sent: list[str] = []
    def send(body: str) -> bool:
        sent.append(body)
        return True
    now = datetime.fromisoformat("2026-10-18T21:30:00+09:00")
    # When: the day's summary runs twice and a late write is carried tomorrow.
    assert auto_write.summarize(now, root=tmp_path, send=send)
    assert not auto_write.summarize(now, root=tmp_path, send=send)
    auto_write.record("plaud", auto_write.Entry(
        "rec-2", "Lifelog/늦은녹음.md", "b" * 40,
        "2026-10-18T22:00:00+09:00", "2026-10-18T22:10:00+09:00",
    ), root=tmp_path)
    assert auto_write.summarize(datetime.fromisoformat("2026-10-19T21:30:00+09:00"),
                                root=tmp_path, send=send)
    # Then: one message per day, grouped by kind, no lost late note.
    assert len(sent) == 2
    assert "2026-10-18" in sent[0] and "Plaud 노트 1" in sent[0]
    assert "- 회의" in sent[0]
    assert "- 늦은녹음" in sent[1]
    assert "- 회의" not in sent[1]


def test_summary_silent_when_empty_and_retries_send_failure(tmp_path: Path) -> None:
    # Given: an empty ledger and a sender that initially fails.
    sent: list[str] = []
    now = datetime.fromisoformat("2026-10-18T21:30:00+09:00")
    def fail(body: str) -> bool:
        sent.append(body)
        return False
    # When: empty, early and failed ticks run.
    assert not auto_write.summarize(now, root=tmp_path, send=fail)
    auto_write.record("plaud", _entry(), root=tmp_path)
    assert not auto_write.summarize(now.replace(hour=20), root=tmp_path, send=fail)
    assert not auto_write.summarize(now, root=tmp_path, send=fail)
    # Then: no successful-day watermark exists, and the next tick retries.
    assert len(sent) == 1
    assert not (tmp_path / "summary.json").exists()
    assert auto_write.summarize(now, root=tmp_path, send=lambda body: True)


def test_release_switch_is_exact_and_node_setting_is_separate(tmp_path: Path) -> None:
    # Given: the release host flag, not a node setting.
    flag = tmp_path / ".hermes/release-flags/auto-write-plaud"
    flag.parent.mkdir(parents=True)
    assert auto_write.release_kinds(tmp_path) == ()
    flag.write_bytes(b"on")
    # When: release planning reads it.
    assert auto_write.release_kinds(tmp_path) == ("plaud",)
    # Then: runtime is still off; a malformed flag is refused.
    assert not auto_write.enabled("plaud", home=tmp_path)
    flag.write_bytes(b"on\n")
    with pytest.raises(ValueError):
        auto_write.release_kinds(tmp_path)


def test_long_summary_stays_one_short_post_without_attachment(tmp_path, monkeypatch):
    # Given: enough note paths to overflow one Discord message.
    for index in range(35):
        auto_write.record("plaud", auto_write.Entry(
            str(index), f"Lifelog/{index}-" + "긴제목" * 20 + ".md",
            "a" * 40, "2026-10-18T09:00:00+09:00", "2026-10-18T10:00:00+09:00",
        ), root=tmp_path)
    calls = []
    def send(content, *, attachments=()):
        assert attachments == ()
        calls.append(content)
        return True
    monkeypatch.setattr("automation.owner_notice.notify_owner", send)
    # When: the actual facade boundary sends the summary.
    assert auto_write.summarize(datetime.fromisoformat("2026-10-18T21:30:00+09:00"), root=tmp_path)
    # Then: one bounded post; the full list remains in the ledger.
    assert len(calls) == 1
    assert len(calls[0]) < 2000
    assert calls[0].count("\n- ") == 5
    assert len((tmp_path / "ledger.jsonl").read_text().splitlines()) == 35

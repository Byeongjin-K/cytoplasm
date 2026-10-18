"""Pending download recovery is not a failed job; new file avoids FS3 case pins."""
from __future__ import annotations

import sqlite3
import sys

import pytest

from tests.unit.test_mail_attachment_retry import (
    FakeDrive, archive, old_state as old_state, syncer,
)


def test_pending_download_tick_is_success_and_silent(old_state, monkeypatch, capsys):
    # Given: the downloader has not recovered yet.
    monkeypatch.setattr(syncer.retry, "fetch_attachment", lambda uid, name: None)
    monkeypatch.setattr(syncer, "drive_client", FakeDrive)
    monkeypatch.setattr(syncer.mail_runtime, "governed_copy_refusal", lambda path: "")
    monkeypatch.setattr(sys, "argv", ["mail_attachment_drive_sync.py"])
    # When: the actual CLI runs a pending retry tick.
    assert syncer.main() == 0
    # Then: the timer has no output to send and the attempt is persisted.
    assert capsys.readouterr().out == ""
    with sqlite3.connect(archive.STATE_DB) as state:
        assert state.execute("SELECT attempts FROM download_retries").fetchone() == (1,)


def test_exhaustion_groups_all_new_items_and_marks_only_after_delivery(old_state, monkeypatch):
    # Given: eight attachments will exhaust in the same tick.
    with sqlite3.connect(old_state) as source:
        for index in range(7):
            source.execute(
                "INSERT INTO attachments VALUES (?,?,NULL,1792000000,'fail',0,'/attachment/1',3)",
                (f"u{index + 2}", f"file-{index}.pdf"),
            )
            source.execute("INSERT INTO messages VALUES (?, '2026-10-15T10:00:00', 'inbox')",
                           (f"u{index + 2}",))
    monkeypatch.setattr(syncer.retry, "fetch_attachment", lambda uid, name: None)
    sent = []
    monkeypatch.setattr(syncer.retry, "notify_exhausted", lambda text: sent.append(text) or True)
    # When: all five budgeted ticks and one later tick run.
    for _ in range(6):
        result = syncer.sync(client=FakeDrive())
        assert result["failed"] == 0
    # Then: one bounded notice covers all items, without losing their budget.
    assert len(sent) == 1
    assert "8건" in sent[0]
    assert len(sent[0]) < 600
    with sqlite3.connect(archive.STATE_DB) as state:
        assert state.execute("SELECT attempts, notified FROM download_retries").fetchall() == [(5, 1)] * 8


def test_old_and_mailon_exhausted_rows_are_silently_out_of_scope(old_state, monkeypatch):
    # Given: one old failure and one recent MailOn-exhausted row.
    with sqlite3.connect(old_state) as source:
        source.execute("UPDATE messages SET recv_date='2024-09-10T10:00:00'")
        source.execute("INSERT INTO messages VALUES ('spent','2026-10-17T10:00:00','inbox')")
        source.execute(
            "INSERT INTO attachments VALUES ('spent','spent.pdf',NULL,1792000000,'fail',5,'/a',3)"
        )
    monkeypatch.setattr(syncer.retry, "fetch_attachment",
                        lambda *args: (_ for _ in ()).throw(AssertionError("out-of-scope fetch")))
    monkeypatch.setattr(syncer.retry, "notify_exhausted",
                        lambda *args: (_ for _ in ()).throw(AssertionError("out-of-scope notice")))
    # When: the sync tick sees both rows.
    result = syncer.sync(client=FakeDrive())
    # Then: no failure, fetch budget or notice is created.
    assert result["failed"] == 0
    with sqlite3.connect(archive.STATE_DB) as state:
        assert state.execute("SELECT COUNT(*) FROM download_retries").fetchone() == (0,)


@pytest.mark.parametrize("received,first_seen,attempts,expected", [
    ("2026-10-15T10:00:00", None, 4, 1),
    ("2026-10-03T10:00:00", None, 0, 0),
    ("2026-10-04T00:00:00+00:00", None, 0, 1),
    (None, "2026-10-15T10:00:00+00:00", 0, 1),
    (None, 1792058400, 0, 1),
    (None, None, 0, 0),
    ("2026-10-19T10:00:00", None, 0, 0),
    ("2026-10-15T10:00:00", None, 5, 0),
])
def test_recent_window_and_mailon_budget_control_fetches(
    old_state, monkeypatch, received, first_seen, attempts, expected,
):
    # Given: a fixed clock and one unresolved row at the scope boundary.
    with sqlite3.connect(old_state) as source:
        source.execute("UPDATE messages SET recv_date=?", (received,))
        source.execute("UPDATE attachments SET first_seen=?, attempts=?", (first_seen, attempts))
    calls = []
    monkeypatch.setattr(syncer.retry, "fetch_attachment", lambda *args: calls.append(args))
    # When: recovery runs inside a real sync tick.
    result = syncer.sync(client=FakeDrive())
    # Then: only recent unspent rows consume one persisted recovery attempt.
    assert len(calls) == expected
    assert result["failed"] == 0
    with sqlite3.connect(archive.STATE_DB) as state:
        assert state.execute("SELECT attempts FROM download_retries").fetchall() == (
            [(1,)] if expected else []
        )

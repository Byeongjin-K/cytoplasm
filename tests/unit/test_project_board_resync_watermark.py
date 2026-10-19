"""Resync regressions live separately to preserve existing test evidence hashes."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path

import pytest

from automation.project_board.config import BoardConfig
from automation.project_board.llm import LlmAnswer, LlmConfig
from automation.project_board.models import ProjectBoardError
from automation.project_board.service import AnalyzeTick, Board, analyze_new_mail
from automation.project_board.sources import MailRuntime

NOW = datetime(2026, 10, 19, tzinfo=UTC)
CONFIG = BoardConfig(llm=LlmConfig(), max_per_tick=1)


@dataclass(slots=True)
class Model:
    """Mutable call recorder; no real model or network is used."""

    name: str = "fake"
    prompts: list[str] = field(default_factory=list)
    error: ProjectBoardError | None = None

    def complete(self, prompt: str) -> LlmAnswer:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return LlmAnswer('{"relevant": false, "reason": "synthetic"}', self.name, "fake")


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MailRuntime:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PROJECT_BOARD_ROOT", str(tmp_path / "board"))
    path = tmp_path / "state.db"
    monkeypatch.setenv("MAIL_WRAPPER_DB", str(path))
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE messages (uid TEXT PRIMARY KEY, folder TEXT NOT NULL, subject TEXT, "
            "sender TEXT, recv_date TEXT, markdown_path TEXT, saved_at INTEGER NOT NULL)"
        )
    return MailRuntime(tmp_path, path)


@pytest.fixture
def board(runtime: MailRuntime) -> Board:
    result = Board(runtime.repo / "board")
    result.root.mkdir()
    (result.root / "analyze-state.json").write_text(json.dumps({
        "mail_received_at": NOW.isoformat(), "mail_received_uids": [],
    }), encoding="utf-8")
    return result


def add_mail(runtime: MailRuntime, uid: str, received_at: str | None) -> None:
    path = f"{uid}.md"
    (runtime.repo / path).write_text(f"---\nsubject: Synthetic {uid}\n---\nBody {uid}", encoding="utf-8")
    with sqlite3.connect(runtime.db) as db:
        db.execute("INSERT INTO messages VALUES (?, 'inbox', '', '', ?, ?, 200)", (uid, received_at, path))


def tick(board: Board, runtime: MailRuntime, model: Model) -> AnalyzeTick:
    return analyze_new_mail(board, runtime, model, CONFIG, today=NOW.date(), post=None)


def test_old_mail_is_not_analyzed_when_resync_refreshes_saved_at(runtime: MailRuntime) -> None:
    # Given: legacy state followed by a resaved historical message with readable content.
    board = Board(runtime.repo / "board")
    board.root.mkdir()
    (board.root / "analyze-state.json").write_text('{"mail_saved_at": 100}', encoding="utf-8")
    (runtime.repo / "old.md").write_text("---\nsubject: Synthetic old mail\n---\nOld body", encoding="utf-8")
    with sqlite3.connect(runtime.db) as db:
        db.execute("INSERT INTO messages VALUES ('old', 'inbox', '', '', ?, 'old.md', 200)",
                   ("2000-01-01T00:00:00+00:00",))
    model = Model()
    # When: the real analysis service ticks against the refreshed mailbox.
    tick = analyze_new_mail(board, runtime, model, BoardConfig(llm=LlmConfig()), today=date(2026, 10, 19), post=None)
    # Then: no historical message reaches analysis, even though it was saved after the old cursor.
    assert model.prompts == [], "resaved old mail was analyzed"
    assert tick.unrelated == 0


def test_resaved_history_is_skipped_after_received_cursor_exists(board: Board, runtime: MailRuntime) -> None:
    # Given: old receipt time but storage time beyond the former cursor.
    add_mail(runtime, "old", "2000-01-01T00:00:00Z")
    model = Model()
    # When: an established board ticks.
    result = tick(board, runtime, model)
    # Then: the old mail does not reach the model.
    assert model.prompts == [] and result.unrelated == 0


def test_new_mail_is_analyzed_once_across_repeated_ticks(board: Board, runtime: MailRuntime) -> None:
    # Given: a new receipt; an unrelated result has no proposal deduplication to mask a replay.
    add_mail(runtime, "new", "2026-10-19T00:00:01Z")
    model = Model()
    # When: repeated ticks consume the same mailbox.
    results = [tick(board, runtime, model) for _ in range(3)]
    # Then: one model invocation and no later work.
    assert len(model.prompts) == 1 and [result.unrelated for result in results] == [1, 0, 0]


def test_ties_survive_tick_limits_and_late_lower_uids(board: Board, runtime: MailRuntime) -> None:
    # Given: multiple receipts in the same second and a one-message tick limit.
    stamp = "2026-10-19T00:00:01Z"
    add_mail(runtime, "b", stamp)
    add_mail(runtime, "c", stamp)
    model = Model()
    # When: pages are consumed and a lower UID arrives later at that timestamp.
    results = [tick(board, runtime, model) for _ in range(2)]
    add_mail(runtime, "a", stamp)
    results.extend(tick(board, runtime, model) for _ in range(2))
    # Then: every tied message is consumed once, including the late lower UID.
    assert [result.unrelated for result in results] == [1, 1, 1, 0]
    assert ["Body b" in model.prompts[0], "Body c" in model.prompts[1], "Body a" in model.prompts[2]] == [True] * 3


@pytest.mark.parametrize("bad_date", [None, "", "invalid", "2026-02-30", "9999-12-31T23:59:59-23:59"])
def test_invalid_receipt_is_skipped_without_blocking_valid_mail(
    board: Board, runtime: MailRuntime, bad_date: str | None, caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: an invalid receipt ahead of a valid one in database insertion order.
    add_mail(runtime, "invalid", bad_date)
    add_mail(runtime, "valid", "2026-10-19T00:00:01Z")
    model = Model()
    # When: selection parses dates before applying the tick limit.
    result = tick(board, runtime, model)
    # Then: only the valid message is analyzed and the rejected date is observable.
    assert result.unrelated == 1 and len(model.prompts) == 1 and "Body valid" in model.prompts[0]
    assert len(caplog.records) == 1 and caplog.records[0].levelname == "WARNING"


def test_offsets_and_fractional_seconds_are_ordered_by_instant(board: Board, runtime: MailRuntime) -> None:
    # Given: lexical date order differs from receipt order.
    add_mail(runtime, "old", "2026-10-19T08:59:59+09:00")
    add_mail(runtime, "later", "2026-10-18T20:00:01-04:00")
    add_mail(runtime, "earlier", "2026-10-19T09:00:00.500000+09:00")
    model = Model()
    # When: ticks consume normalized receipts.
    results = [tick(board, runtime, model) for _ in range(3)]
    # Then: subsecond mail is not rounded away and offset mail is chronologically ordered.
    assert [result.unrelated for result in results] == [1, 1, 0]
    assert "Body earlier" in model.prompts[0] and "Body later" in model.prompts[1]


def test_naive_mailon_receipts_use_the_runtime_timezone(
    board: Board, runtime: MailRuntime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: MailOn's timezone-less local dates and a UTC received cursor.
    add_mail(runtime, "old", "2026-10-19T08:59:59")
    add_mail(runtime, "new", "2026-10-19T09:00:01")
    model = Model()
    # When: the analysis runtime uses the same local timezone as the collector.
    try:
        with monkeypatch.context() as environment:
            environment.setenv("TZ", "Asia/Seoul")
            time.tzset()
            result = tick(board, runtime, model)
    finally:
        time.tzset()
    # Then: only the receipt after UTC midnight reaches analysis.
    assert result.unrelated == 1 and len(model.prompts) == 1 and "Body new" in model.prompts[0]


def test_retryable_failure_preserves_the_received_cursor(board: Board, runtime: MailRuntime) -> None:
    # Given: the model temporarily fails for the next receipt.
    add_mail(runtime, "new", "2026-10-19T00:00:01Z")
    before = (board.root / "analyze-state.json").read_bytes()
    model = Model(error=ProjectBoardError("synthetic outage", retryable=True))
    # When: the tick fails temporarily.
    result = tick(board, runtime, model)
    # Then: no cursor advancement hides the mail from the retry.
    assert result.deferred and (board.root / "analyze-state.json").read_bytes() == before
    model.error = None
    assert tick(board, runtime, model).unrelated == 1


def test_unreadable_mail_advances_once_without_model_call(board: Board, runtime: MailRuntime) -> None:
    # Given: a valid receipt whose stored body is missing.
    add_mail(runtime, "missing", "2026-10-19T00:00:01Z")
    (runtime.repo / "missing.md").unlink()
    model = Model()
    # When: subsequent ticks see the same missing document.
    results = [tick(board, runtime, model) for _ in range(2)]
    # Then: it is skipped once and never sent to the model.
    assert [result.skipped for result in results] == [1, 0] and model.prompts == []


@pytest.mark.parametrize("legacy", [False, True])
def test_initialization_uses_now_not_latest_storage_time(
    runtime: MailRuntime, monkeypatch: pytest.MonkeyPatch, legacy: bool,
) -> None:
    from automation.project_board import service

    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return NOW.astimezone(tz)

    # Given: stale mailbox history, optional legacy state, and a fixed tick clock.
    monkeypatch.setattr(service, "datetime", Clock)
    board = Board(runtime.repo / "board")
    if legacy:
        board.root.mkdir()
        (board.root / "analyze-state.json").write_text('{"mail_saved_at": 100}', encoding="utf-8")
    add_mail(runtime, "old", "2000-01-01T00:00:00Z")
    model = Model()
    # When: initialization is followed by resync and a genuinely new receipt.
    first = tick(board, runtime, model)
    with sqlite3.connect(runtime.db) as db:
        db.execute("UPDATE messages SET saved_at = 300")
    add_mail(runtime, "new", "2026-10-19T00:00:01Z")
    result = tick(board, runtime, model)
    # Then: initialization never analyzes history, and new receipt time is the only trigger.
    assert first.initialised and result.unrelated == 1 and len(model.prompts) == 1
    assert "Body new" in model.prompts[0] and tick(board, runtime, model).unrelated == 0


def test_disabled_cron_keeps_legacy_state_untouched(runtime: MailRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation.project_board.cron import project_board_analyze as cron

    # Given: opt-out and legacy state, with the real cron's filesystem rooted in tmp_path.
    board = Board(runtime.repo / "board")
    board.root.mkdir()
    state = board.root / "analyze-state.json"
    state.write_text('{"mail_saved_at": 100}', encoding="utf-8")
    (board.root / "config.json").write_text('{"analyze": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setattr(cron, "_load_env_secrets", lambda: None)
    # When: the real cron entry is called.
    result = cron.main()
    # Then: the disabled cron does not migrate state or create an analysis lock.
    assert result == 0 and json.loads(state.read_text()) == {"mail_saved_at": 100}
    assert not (board.root / "analyze.lock").exists()

"""10-minute ticks: no overlap, quiet counted in workday hours only, and YooDori's --now."""
from __future__ import annotations

import fcntl
import json

from tests.unit.test_mail_sync_watch import _isolate as _isolate  # noqa: F401 — shared isolation
from tests.unit.test_mail_sync_watch import _script, watch

_EMPTY = (0, json.dumps({"status": "ok", "sync": {"exit_code": 0, "new_mails": 0}}))


def test_evening_and_weekend_ticks_never_open_the_quiet_alert(monkeypatch, capsys) -> None:
    # Given: empty collections on a Saturday afternoon and a weekday evening.
    monkeypatch.setenv(watch.QUIET_AFTER_ENV, "1")
    for moment in (watch.datetime(2026, 10, 17, 15, 0, tzinfo=watch.KST),
                   watch.datetime(2026, 10, 19, 21, 0, tzinfo=watch.KST)):
        monkeypatch.setattr(watch, "_now", lambda moment=moment: moment)
        _script(monkeypatch, [_EMPTY])
        # When / Then: nothing is said.
        assert watch.main() == 0
        assert capsys.readouterr().out.strip() == ""


def test_a_tick_skips_while_a_collection_holds_the_lock(monkeypatch, tmp_path) -> None:
    # Given: an on-request collection holds the lock.
    calls = _script(monkeypatch, [_EMPTY])
    with open(tmp_path / "mail-sync.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        # When / Then: the tick does not start a second login.
        assert watch.main() == 0
    assert calls == []


def test_now_collects_once_and_reports_new_mail(monkeypatch, capsys) -> None:
    # Given: a free lock and one new mail.
    calls = _script(monkeypatch, [(0, json.dumps({"status": "ok", "sync": {"exit_code": 0, "new_mails": 1}}))])
    # When / Then: one collection runs and YooDori gets the count.
    assert watch.now() == 0
    assert capsys.readouterr().out.strip() == "MAIL-SYNC-NOW ok new=1"
    assert len(calls) == 1 and "--sync" in calls[0]


def test_now_gives_up_politely_when_the_lock_stays_busy(monkeypatch, capsys, tmp_path) -> None:
    # Given: a collection that does not finish within the wait.
    monkeypatch.setattr(watch, "NOW_WAIT_SECONDS", 0)
    with open(tmp_path / "mail-sync.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        # When / Then: --now says so instead of logging in twice.
        assert watch.now() == 1
    assert capsys.readouterr().out.startswith("MAIL-SYNC-NOW busy")

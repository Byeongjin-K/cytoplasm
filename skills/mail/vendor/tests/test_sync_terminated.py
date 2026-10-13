"""A terminated sync records its run and closes its browser (2026-10-13 15:44 KST).

The caller's timeout killed two syncs mid-run: their rows stayed ``running`` and the
browser stayed up for 15 hours. SIGTERM now ends the sync through its normal failure
path, so ``finally`` closes the browser and the run is recorded as failed."""
from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from mailon import main as mailon_main


def test_sigterm_during_sync_fails_the_run_and_closes_the_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a sync whose login is interrupted by the caller's SIGTERM
    finished: list[tuple[int, str, str]] = []
    closed: list[bool] = []

    class _Cfg:
        logs_dir = tmp_path / "logs"
        state_db_path = tmp_path / "state.db"

    class _Db:
        def __init__(self, _path) -> None:
            pass

        def start_run(self) -> int:
            return 7

        def finish_run(self, run_id, *, status, new_mails=0, error=None) -> None:
            finished.append((run_id, status, error or ""))

    class _Browser:
        def close(self) -> None:
            closed.append(True)

    def login(_browser, _cfg) -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(mailon_main, "load_config", lambda: _Cfg)
    monkeypatch.setattr(mailon_main, "_setup_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(mailon_main, "StateDB", _Db)
    monkeypatch.setattr(mailon_main, "_make_browser", lambda _cfg: _Browser())
    monkeypatch.setattr(mailon_main, "login", login)
    args = type("Args", (), {"folders": "inbox", "limit": 1, "full_scan": False})()

    # When: the sync runs
    try:
        rc = mailon_main.cmd_sync(args)
    finally:
        signal.signal(signal.SIGTERM, previous)

    # Then: the run is closed as failed and the browser was closed
    assert rc == 3
    assert finished and finished[0][:2] == (7, "fail") and "terminated" in finished[0][2]
    assert closed == [True]

"""A sync that died without finishing does not stay ``running`` forever (2026-10-13).

Two syncs killed by the 900 s timeout left their rows ``running`` with no end, so the
runs table could not say whether collection was alive. The next run closes such rows.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"))

from mailon.state import STALE_RUN_SECONDS, StateDB  # noqa: E402


def _rows(path: Path) -> dict[int, tuple[str, str | None]]:
    with sqlite3.connect(path) as conn:
        return {run_id: (status, error) for run_id, status, error in
                conn.execute("SELECT run_id, status, error FROM runs")}


def test_a_new_run_closes_runs_that_never_finished(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    db = StateDB(path)
    dead = db.start_run()
    alive = db.start_run()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE runs SET started_at = ? WHERE run_id = ?",
                     (int(time.time()) - STALE_RUN_SECONDS - 60, dead))

    current = db.start_run()

    rows = _rows(path)
    assert rows[dead][0] == "fail" and "abandoned" in (rows[dead][1] or "")
    assert rows[alive] == ("running", None)
    assert rows[current] == ("running", None)

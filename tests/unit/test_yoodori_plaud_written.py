"""YooDori reads which Plaud notes were written so it can post one line each (owner 2026-10-19).

New file so the recorded gate read cases keep their byte-for-byte argv pins.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate


def _ledger(home: Path, rows: list[dict]) -> None:
    path = home / ".hermes/auto-write/ledger.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows) + "not json\n",
                    encoding="utf-8")


def test_plaud_written_lists_only_plaud_notes_newest_last(tmp_path: Path, monkeypatch, capsys) -> None:
    # Given: a ledger with Plaud notes among other auto writes.
    home = tmp_path / "home"
    _ledger(home, [
        {"kind": "plaud", "source_id": "of_1", "path": "000_PARA/Area/Lifelog/2026/a_첫.md",
         "recorded_at": "2026-10-19T01:00:00", "written_at": "2026-10-19T02:00:00+00:00"},
        {"kind": "todo", "source_id": "t", "path": "할 일"},
        {"kind": "plaud", "source_id": "of_2", "path": "000_PARA/Area/Lifelog/2026/b_둘.md",
         "recorded_at": "2026-10-19T03:00:00", "written_at": "2026-10-19T04:00:00+00:00"},
    ])
    monkeypatch.setattr(gate, "HOME", home)
    monkeypatch.setattr(gate, "_log", lambda *a: None)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", shlex.join(["plaud-written", "1"]))
    # When: YooDori asks for the latest one.
    assert gate.main() == 0
    # Then: only that Plaud note, by name and path, and no body.
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "ok", "notes": [{
        "id": "of_2", "title": "b_둘", "path": "000_PARA/Area/Lifelog/2026/b_둘.md",
        "recorded_at": "2026-10-19T03:00:00", "written_at": "2026-10-19T04:00:00+00:00"}]}


def test_plaud_written_without_a_ledger_is_empty(tmp_path: Path) -> None:
    # Given / When / Then: no ledger yet is an empty list, not an error.
    assert gate.plaud_written(tmp_path, 5) == {"status": "ok", "notes": []}


@pytest.mark.parametrize("words", [["plaud-written"], ["plaud-written", "0"], ["plaud-written", "51"],
                                   ["plaud-written", "3", "x"]])
def test_plaud_written_takes_exactly_one_count(words: list[str]) -> None:
    # Given / When / Then: a missing, out-of-range or extra argument is refused.
    with pytest.raises(gate.Refused):
        gate.build(words)

"""YooDori gate `mail-search <query...> [--limit n]` (2026-10-26): find institutional mail beyond
the latest 50 by subject, sender or saved body.

New file like test_yoodori_gate_mail_resolve.py: one new read verb, while test_yoodori_gate.py pins
the original read argv byte for byte.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate

PY = "/usr/bin/python3"
WRAPPER = "/srv/autophagy-skills/live/mail/scripts/mail_wrapper.py"
WORD = "연구비카드"


@pytest.fixture
def gate_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    log = tmp_path / "yoodori-gate.log"
    calls: list[list[str]] = []
    monkeypatch.setattr(gate, "LOG", log)

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)

    def run(command: str) -> tuple[int, list[list[str]], str]:
        monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command)
        rc = gate.main()
        return rc, calls, log.read_text(encoding="utf-8")

    return run


def test_one_word_runs_the_read_only_wrapper_search_with_default_limit() -> None:
    assert gate.build(["mail-search", WORD]) == (
        "mail-search", [PY, WRAPPER, "search", "--limit", "20", "--query", WORD], gate.HOME)


def test_several_words_and_a_trailing_limit() -> None:
    _name, argv, _cwd = gate.build(["mail-search", WORD, "NK264F", "--limit", "5"])
    assert argv == [PY, WRAPPER, "search", "--limit", "5", "--query", f"{WORD} NK264F"]


@pytest.mark.parametrize("words", [
    ["mail-search"],
    ["mail-search", "--limit", "5"],
    ["mail-search", WORD, "--limit", "0"],
    ["mail-search", WORD, "--limit", "51"],
    ["mail-search", WORD, "--limit", "x"],
    ["mail-search", WORD, "--limit"],
    ["mail-search", "--limit", "5", WORD],
    ["mail-search", WORD, "--body"],
    ["mail-search", "-x", WORD],
    ["mail-search", "가" * 201],
])
def test_missing_query_bad_limit_flags_or_long_queries_are_refused(words: list[str]) -> None:
    with pytest.raises(gate.Refused):
        gate.build(words)


def test_gate_runs_it_and_logs_only_name_rc_seconds(gate_run) -> None:
    rc, calls, log = gate_run(f"mail-search {WORD} NK264F --limit 30")
    assert rc == 0
    assert calls == [[PY, WRAPPER, "search", "--limit", "30", "--query", f"{WORD} NK264F"]]
    assert re.fullmatch(r"\S+ mail-search rc=0 s=\d+\.\d\n", log)
    assert WORD not in log and "NK264F" not in log


@pytest.mark.parametrize("command", [
    f"mail-search {WORD};id", f"mail-search $({WORD})", f"mail-search {WORD} | cat",
    f"mail-search {WORD} > /tmp/x",
])
def test_shell_metacharacters_never_reach_the_wrapper(gate_run, command: str) -> None:
    rc, calls, log = gate_run(command)
    assert rc == 2
    assert calls == []
    assert re.fullmatch(r"\S+ refused rc=2 s=\d+\.\d\n", log)
    assert WORD not in log

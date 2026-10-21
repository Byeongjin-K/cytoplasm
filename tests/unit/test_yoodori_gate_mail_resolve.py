"""YooDori gate `mail-resolve <name>` (owner Q344, 2026-10-21): name -> institute mail address.

New file rather than more cases in test_yoodori_gate.py (pins the original read argv byte
for byte) or test_yoodori_gate_jobs.py (owner-command jobs); this is one new read verb.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate

PY = "/usr/bin/python3"
WRAPPER = "/srv/autophagy-skills/live/mail/scripts/mail_wrapper.py"
NAME = "홍길동"


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


def test_one_name_runs_the_read_only_wrapper_resolve() -> None:
    # Given / When
    name, argv, cwd = gate.build(["mail-resolve", NAME])
    # Then
    assert (name, argv, cwd) == ("mail-resolve", [PY, WRAPPER, "resolve", "--name", NAME], gate.HOME)


@pytest.mark.parametrize("words", [
    ["mail-resolve"],
    ["mail-resolve", NAME, "extra"],
    ["mail-resolve", "--send"],
    ["mail-resolve", "가" * 41],
])
def test_missing_extra_flag_or_long_names_are_refused(words: list[str]) -> None:
    with pytest.raises(gate.Refused):
        gate.build(words)


def test_gate_runs_it_and_logs_only_name_rc_seconds(gate_run) -> None:
    # When: YooDori's forced command carries one name.
    rc, calls, log = gate_run(f"mail-resolve {NAME}")
    # Then: the wrapper ran once, and the log holds no part of the name.
    assert rc == 0
    assert calls == [[PY, WRAPPER, "resolve", "--name", NAME]]
    assert re.fullmatch(r"\S+ mail-resolve rc=0 s=\d+\.\d\n", log)
    assert NAME not in log


@pytest.mark.parametrize("command", [f"mail-resolve {NAME};id", f"mail-resolve $({NAME})"])
def test_shell_metacharacters_never_reach_the_wrapper(gate_run, command: str) -> None:
    rc, calls, log = gate_run(command)
    assert rc == 2
    assert calls == []
    assert re.fullmatch(r"\S+ refused rc=2 s=\d+\.\d\n", log)
    assert NAME not in log

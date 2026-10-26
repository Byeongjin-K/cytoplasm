"""YooDori gate `card-withdraw <draft id> <reason...>` (2026-10-26, owner Q367).

New file like test_yoodori_gate_mail_search.py: test_yoodori_gate.py pins the original argv byte
for byte. A card counts as YooDori's only when the gate's own log shows a mail-compose or mail-draft
call that succeeded while that draft was created; every other card is refused before any child runs.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate

PY = "/usr/bin/python3"
TRIAGE = "/srv/autophagy-skills/live/mail/scripts/triage_cli.py"
DRAFT = "28fe93"
REASON = "시험 메일이라 필요 없어짐"


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    log = tmp_path / "yoodori-gate.log"
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    monkeypatch.setattr(gate, "LOG", log)
    monkeypatch.setattr(gate, "MAIL_DRAFTS", drafts)

    def put(draft_id: str, created: str, *log_lines: str) -> None:
        (drafts / f"{draft_id}.json").write_text(
            json.dumps({"id": draft_id, "created": created, "status": "pending"}), encoding="utf-8")
        with log.open("a", encoding="utf-8") as handle:
            handle.writelines(line + "\n" for line in log_lines)

    return put, log


def _argv(draft_id: str = DRAFT, reason: str = REASON) -> list[str]:
    return [PY, TRIAGE, "withdraw", "--draft", draft_id, "--reason", reason, "--by", "유돌"]


@pytest.mark.parametrize("line", [
    "2026-10-26T09:47:40Z mail-compose rc=0 s=3.4",
    "2026-10-26T09:47:37Z mail-compose rc=0 s=0.2",
    "2026-10-26T09:48:10Z mail-draft rc=0 s=40.0",
])
def test_a_card_the_gate_posted_runs_only_the_withdraw_subcommand(node, line: str) -> None:
    put, _log = node
    put(DRAFT, "2026-10-26T09:47:37Z", "2026-10-26T09:40:00Z calendar rc=0 s=0.5", line)
    name, argv, cwd = gate.build(["card-withdraw", DRAFT, *REASON.split()])
    assert (name, argv, cwd) == ("card-withdraw", _argv(), gate.RELEASE)


@pytest.mark.parametrize("line", [
    "2026-10-26T09:47:30Z mail-compose rc=0 s=1.0",
    "2026-10-26T09:47:40Z mail-compose rc=2 s=3.4",
    "2026-10-26T09:47:40Z mail-search rc=0 s=3.4",
    "2026-10-26T09:47:40Z card-withdraw rc=0 s=3.4",
    "2026-10-26T09:49:00Z mail-compose rc=0 s=3.4",
])
def test_a_card_the_gate_did_not_post_is_refused(node, line: str) -> None:
    put, _log = node
    put(DRAFT, "2026-10-26T09:47:37Z", line)
    with pytest.raises(gate.Refused, match="YooDori"):
        gate.build(["card-withdraw", DRAFT, REASON])


def test_a_card_without_any_gate_log_is_refused(node) -> None:
    put, log = node
    put(DRAFT, "2026-10-26T09:47:37Z")
    log.unlink()
    with pytest.raises(gate.Refused):
        gate.build(["card-withdraw", DRAFT, REASON])


@pytest.mark.parametrize("words", [
    ["card-withdraw"],
    ["card-withdraw", DRAFT],
    ["card-withdraw", "abcdef", REASON],
    ["card-withdraw", "28FE93", REASON],
    ["card-withdraw", "28fe9", REASON],
    ["card-withdraw", "../x", REASON],
    ["card-withdraw", "v2.7.2", REASON],
    ["card-withdraw", DRAFT, "--approve"],
    ["card-withdraw", DRAFT, "-x", REASON],
    ["card-withdraw", DRAFT, "가" * 201],
])
def test_unknown_ids_malformed_ids_and_bad_reasons_are_refused(node, words: list[str]) -> None:
    put, _log = node
    put(DRAFT, "2026-10-26T09:47:37Z", "2026-10-26T09:47:40Z mail-compose rc=0 s=3.4")
    with pytest.raises(gate.Refused):
        gate.build(words)


@pytest.fixture
def gate_run(node, monkeypatch: pytest.MonkeyPatch):
    put, log = node
    put(DRAFT, "2026-10-26T09:47:37Z", "2026-10-26T09:47:40Z mail-compose rc=0 s=3.4")
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(gate, "card_env", lambda *_args: {"DISCORD_BOT_TOKEN": "x"})

    def run(command: str) -> tuple[int, list[tuple[list[str], dict]], str]:
        monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command)
        rc = gate.main()
        return rc, calls, log.read_text(encoding="utf-8").splitlines()[-1]

    return run


def test_gate_runs_it_with_the_card_credentials_and_logs_no_reason(gate_run) -> None:
    rc, calls, last = gate_run(f"card-withdraw {DRAFT} {REASON}")
    assert rc == 0
    assert [argv for argv, _ in calls] == [_argv()]
    assert calls[0][1]["env"] == {"DISCORD_BOT_TOKEN": "x"}
    assert re.fullmatch(r"\S+ card-withdraw rc=0 s=\d+\.\d", last)
    assert "시험" not in last


@pytest.mark.parametrize("command", [
    f"card-withdraw {DRAFT};id", f"card-withdraw {DRAFT} $(id)", f"card-withdraw {DRAFT} | cat",
    f"card-withdraw {DRAFT} `id`", f"card-withdraw {DRAFT} > /tmp/x",
])
def test_shell_metacharacters_are_refused_before_any_child(gate_run, command: str) -> None:
    rc, calls, last = gate_run(command)
    assert rc == 2 and calls == []
    assert last.split()[1] == "refused"

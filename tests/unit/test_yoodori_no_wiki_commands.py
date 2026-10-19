"""The gate has no wiki commands any more (owner 2026-10-19 17:43 KST).

"위키에 쓰라는 요청을 처리하는것도 없애야 하는거 아니냐?": notes are ordinary Obsidian notes, so
wiki-write, wiki-search and wiki-get are gone, not refused with a hint. New file so the
recorded gate cases keep their shape.
"""
from __future__ import annotations

import shlex

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate

ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]


@pytest.mark.parametrize("words", [
    ["wiki-write", "--title", "t", "--body-b64", "Yg==", *ORIGIN],
    ["wiki-search", "실험"],
    ["wiki-get", "lab-rule"],
])
def test_wiki_commands_are_unknown(words: list[str], monkeypatch, capsys) -> None:
    # Given / When: YooDori sends an old wiki command.
    monkeypatch.setattr(gate, "_log", lambda *a: None)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", shlex.join(words))
    # Then: refused as an unknown command, and the help names no wiki command.
    assert gate.main() == 2
    err = capsys.readouterr().err
    assert f"unknown command: {words[0]}" in err
    assert "wiki" not in gate.USAGE and "wiki-write" not in gate.JOB_COMMANDS

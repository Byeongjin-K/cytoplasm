"""Forced SSH command: unchanged reads and fixed, validated card producers only."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate

REPO = Path(__file__).resolve().parents[2]
PY = "/usr/bin/python3"
LIVE = "/srv/autophagy-skills/live"
ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.mark.parametrize("words, argv, cwd", [
    (["calendar", "07", "실험"], [PY, f"{LIVE}/calendar/scripts/calendar_cli.py",
                                  "list", "--days", "7", "--query", "실험"], gate.HOME),
    (["calendar", "31"], [PY, f"{LIVE}/calendar/scripts/calendar_cli.py", "list", "--days", "31"], gate.HOME),
    (["mail-list", "050"], [PY, f"{LIVE}/mail/scripts/mail_wrapper.py", "list", "--limit", "50"], gate.HOME),
    (["mail-get", "123", "--body"], [PY, f"{LIVE}/mail/scripts/mail_wrapper.py", "get", "123", "--body"], gate.HOME),
    (["mail-get", "123"], [PY, f"{LIVE}/mail/scripts/mail_wrapper.py", "get", "123"], gate.HOME),
    (["recall", "개인", "기억"], [PY, f"{LIVE}/recall/scripts/recall_cli.py", "search", "개인 기억"], gate.HOME),
    (["status"], [PY, "-m", "automation.doctor"], gate.RELEASE),
])
def test_read_argv_is_unchanged(words: list[str], argv: list[str], cwd: Path) -> None:
    # Given / When / Then: full byte-equivalent argv, not only a subcommand pin.
    assert gate.build(words) == (words[0], argv, cwd)


@pytest.fixture
def staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(gate, "STAGING", tmp_path / "staging")
    gate.STAGING.mkdir()
    path = gate.STAGING / "note.md"
    path.write_text("# 노트\n본문", encoding="utf-8")
    return path


def requests(staged: Path) -> list[tuple[str, list[str], list[str]]]:
    mail = ["--to", "reader@example.invalid", "--cc", "cc@example.invalid",
            "--bcc", "bcc@example.invalid", "--subject", "안내",
            "--body-b64", b64("첫 줄 & <둘째>\n본문; $100"), "--in-reply-to", "123"]
    return [
        ("mail-compose", mail, [PY, f"{LIVE}/mail/scripts/triage_cli.py", "compose",
         *mail[:8], "--body", "첫 줄 & <둘째>\n본문; $100", *mail[10:], *ORIGIN]),
        ("calendar-request", ["--text", "내일 오후 3시 실험", "--summary", "실험"],
         [PY, f"{LIVE}/calendar/scripts/calendar_cli.py", "draft-create",
          "--text", "내일 오후 3시 실험", "--summary", "실험", *ORIGIN]),
        ("todo-request", ["--title", "정리", "--notes-b64", b64("노트\n정리"),
                          "--due", "2026-11-01T00:00:00Z"],
         [PY, f"{LIVE}/todo/scripts/todo_cli.py", "request", "--title", "정리",
          "--notes", "노트\n정리", "--due", "2026-11-01T00:00:00Z", *ORIGIN]),
        ("drive-save", ["--file", str(staged), "--summary", "파일 저장", "--project", "예시"],
         [PY, "-m", "automation.effect_request", "request", "drive-save", "--file",
          str(staged), "--summary", "파일 저장", "--project", "예시", *ORIGIN]),
        ("command-request", ["--summary", "문구 출력", "--target", "출력",
                             "--argv-b64", b64(json.dumps(["/usr/bin/printf", "hello"]))],
         [PY, "-m", "automation.effect_request", "request", "command",
          "--summary", "문구 출력", "--target", "출력", *ORIGIN, "--", "/usr/bin/printf", "hello"]),
        ("obsidian-request", ["--relpath", "000_PARA/Resource/note.md",
                              "--body-file", str(staged), "--title", "노트"],
         [PY, "-m", "automation.obsidian_write", "request", "--relpath",
          "000_PARA/Resource/note.md", "--body-file", str(staged), "--title", "노트", *ORIGIN]),
    ]


def test_all_card_commands_build_real_request_cli_argv(staged: Path) -> None:
    # Given: each supported public request command and its real producer parser.
    cases = requests(staged)
    assert {name for name, _, _ in cases} == set(gate.CARD_COMMANDS)
    for name, args, expected in cases:
        # When
        built = gate.build([name, *args, *ORIGIN])
        # Then: parse in an isolated process so same-named skill modules cannot leak.
        assert built == (name, expected, gate.RELEASE)
        if expected[1] == "-m":
            module, rest, folder = expected[2] + ".cli", expected[3:], REPO
            if name == "obsidian-request":
                module = "automation.obsidian_write.note_request_cli"
        else:
            module, rest, folder = Path(expected[1]).stem, expected[2:], REPO / "skills" / expected[1].split("/")[-3] / "scripts"
        script = ("import sys, importlib; sys.path.insert(0, sys.argv[1]); "
                  "m=importlib.import_module(sys.argv[2]); m.build_parser().parse_args(sys.argv[3:])")
        parsed = subprocess.run([sys.executable, "-c", script, str(folder), module, *rest],
                                cwd=REPO, capture_output=True, text=True, check=False)
        assert parsed.returncode == 0, parsed.stderr


@pytest.mark.parametrize("args", [
    ["--to", "a@b.invalid", "--subject", "s", "--body", "b"],
    ["--to", "not-an-email", "--subject", "s", "--body", "b", *ORIGIN],
    ["--to", "a@b.invalid,c@d.invalid", "--subject", "s", "--body", "b", *ORIGIN],
    ["--to", "a@b.invalid", "--subject", "s" * 241, "--body", "b", *ORIGIN],
    ["--to", "a@b.invalid", "--subject", "s", "--body-b64", "!", *ORIGIN],
    ["--to", "a@b.invalid", "--subject", "s", "--body-b64", b64("\x00"), *ORIGIN],
    ["--to", "a@b.invalid", "--subject", "s", "--body", "b", *ORIGIN, "--no-post", "yes"],
    ["--to", "a@b.invalid", "--subject", "s", "--body", "b", *ORIGIN, "--subject", "again"],
    ["--to", "a@b.invalid", "--subject", "s", "--body", "b", "--origin-channel-id", "abc",
     "--origin-message-id", "123"],
    ["--to", "a@b.invalid", "--subject", "s", "--body", "b", "--origin-channel-id", "0",
     "--origin-message-id", "123"],
])
def test_invalid_mail_values_are_refused(args: list[str]) -> None:
    # Given / When / Then
    with pytest.raises(gate.Refused):
        gate.build(["mail-compose", *args])


@pytest.mark.parametrize("command", [
    ["calendar", "0"], ["calendar", "32"], ["mail-list", "51"],
    ["mail-get", "../123"], ["status", "extra"], ["recall", "x" * 301],
    ["todo-request", "--title", "t", "--due", "tomorrow", *ORIGIN],
    ["todo-request", "--title", "t", "--due", "2026-11-01", *ORIGIN],
    ["obsidian-request", "--relpath", "../../secret.md", "--body-file", "/etc/passwd", *ORIGIN],
    ["drive-save", "--file", "/etc/passwd", "--summary", "s", *ORIGIN],
    ["wiki-draft", "--title", "t", "--body", "b", *ORIGIN],
])
def test_invalid_inputs_are_refused(command: list[str]) -> None:
    # Given / When / Then
    with pytest.raises(gate.Refused):
        gate.build(command)


@pytest.mark.parametrize("kind", ["symlink", "parent-symlink", "traversal", "directory", "missing", "large"])
def test_only_staged_regular_files_are_accepted(
    staged: Path, tmp_path: Path, kind: str,
) -> None:
    # Given
    path = staged
    if kind == "symlink":
        path = staged.with_name("alias")
        path.symlink_to(staged)
    elif kind == "parent-symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret").write_text("private")
        (gate.STAGING / "alias").symlink_to(outside, target_is_directory=True)
        path = gate.STAGING / "alias/secret"
    elif kind == "traversal":
        path = gate.STAGING / "../staging/note.md"
    elif kind == "directory":
        path = gate.STAGING
    elif kind == "missing":
        path = gate.STAGING / "absent"
    else:
        with path.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
    # When / Then
    with pytest.raises(gate.Refused):
        gate.build(["drive-save", "--file", str(path), "--summary", "s", *ORIGIN])


@pytest.mark.parametrize("raw", [
    *sorted(gate.REFUSED_WORDS), "mailon send", "mail send", "calendar confirm",
    "todo create", "effect_request watch", "/tmp/confirm_reaction_watch.py",
    "mail-compose --yes", "deploy.sh", "python3 /tmp/watch.py",
    "status;id", "status&id", "status|id", "status`id`", "status$(id)",
    "status>file", "status<file", "status\\x", "status\n", "status\x00",
])
def test_execution_and_shell_commands_are_refused_before_spawn(
    raw: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: do not let any real process or home log be reached.
    monkeypatch.setattr(gate.os, "environ", {"SSH_ORIGINAL_COMMAND": raw})
    monkeypatch.setattr(gate, "_log", lambda *_: None)
    def no_spawn(*args: str, **kwargs: str) -> None:
        pytest.fail("refused command spawned a child")
    monkeypatch.setattr(gate.subprocess, "run", no_spawn)
    # When / Then
    assert gate.main() == 2
    assert "YOODORI-GATE-REFUSED" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["/usr/bin/python3", "-c", "print(1)"], ["/usr/bin/bash", "-c", "id"],
    ["/usr/bin/mailon", "send"], ["/usr/bin/id", "--yes"], ["/usr/bin/printf", "x;id"],
    ["/tmp/direct_watch.py"], ["/usr/bin/x", "apply"], ["/usr/bin/deploy-tool"],
])
def test_encoded_execution_family_is_refused(argv: list[str]) -> None:
    # Given / When / Then
    with pytest.raises(gate.Refused):
        gate.build(["command-request", "--summary", "s", "--target", "t",
                    "--argv-b64", b64(json.dumps(argv)), *ORIGIN])

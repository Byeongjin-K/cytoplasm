"""Gmail reads produce compact JSON and cannot reach a gws write operation."""
from __future__ import annotations

import base64
import json
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.yoodori_gate import gmail_read as gmail
from automation.yoodori_gate import yoodori_read_gate as gate

ID = "abcdef0123456789"
OTHER = "fedcba9876543210"


@pytest.mark.parametrize("words, expected", [
    (["gmail-list", "050"], ["list", "50"]),
    (["gmail-list", "2", "from:a@example.invalid", "is:unread"],
     ["list", "2", "from:a@example.invalid is:unread"]),
    (["gmail-get", ID], ["get", ID]),
    (["gmail-get", ID, "--body"], ["get", ID, "--body"]),
])
def test_gate_builds_only_the_gmail_read_helper(words: list[str], expected: list[str]) -> None:
    # Given / When / Then
    assert gate.build(words) == (
        words[0], ["/usr/bin/python3", str(Path(gate.__file__).with_name("gmail_read.py")), *expected], gate.HOME)
    assert set(gate.GMAIL_READ_COMMANDS) == {"gmail-list", "gmail-get"}


@pytest.mark.parametrize("words", [
    ["gmail-list"], ["gmail-list", "0"], ["gmail-list", "51"], ["gmail-list", "2", "x" * 201],
    ["gmail-get"], ["gmail-get", "abcdef0"], ["gmail-get", "a" * 33],
    ["gmail-get", "ABCDEF012345"], ["gmail-get", "../" + ID], ["gmail-get", ID, "--raw"],
])
def test_bad_gmail_arguments_are_gate_refusals(words: list[str]) -> None:
    # Given / When / Then
    with pytest.raises(gate.Refused):
        gate.build(words)


WRITES = ("send", "drafts", "modify", "labels", "trash", "delete", "batchModify",
          "insert", "import", "watch", "settings")


@pytest.mark.parametrize("operation", WRITES)
@pytest.mark.parametrize("template", [
    "gmail {operation}", "gmail-{operation}", "gmail-list 2 --{operation}",
    f"gmail-get {ID} {{operation}}", "gws gmail users messages {operation}",
])
def test_every_gmail_write_is_refused_before_spawn(
    operation: str, template: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", template.format(operation=operation))
    monkeypatch.setattr(gate, "_log", lambda *_: None)
    def no_spawn(*_args: str, **_kwargs: str) -> None:
        pytest.fail("Gmail write reached a child")
    monkeypatch.setattr(gate.subprocess, "run", no_spawn)
    # When / Then
    assert gate.main() == 2
    assert "YOODORI-GATE-REFUSED" in capsys.readouterr().err


@pytest.mark.parametrize("query", ["x;id", "x&id", "x|id", "x`id`", "x$(id)", "x>f", "x<f", "x\\f"])
def test_gmail_query_keeps_the_raw_shell_refusal(
    query: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", shlex.join(["gmail-list", "2", query]))
    monkeypatch.setattr(gate, "_log", lambda *_: None)
    # When / Then
    assert gate.main() == 2
    assert "YOODORI-GATE-REFUSED" in capsys.readouterr().err


def _message(mid: str, html: bool = False) -> dict[str, gmail.Json]:
    content = "<p>안녕 &amp; 본문</p>" if html else "안녕\n본문"
    return {
        "id": mid, "snippet": "한 줄 요약",
        "payload": {"mimeType": "multipart/mixed", "headers": [
            {"name": "From", "value": "reader@example.invalid"},
            {"name": "Subject", "value": "제목"}, {"name": "Date", "value": "Mon, 19 Oct 2026"},
        ], "parts": [
            {"mimeType": "application/octet-stream", "filename": "private.txt",
             "body": {"attachmentId": "never-fetch"}},
            {"mimeType": "text/html" if html else "text/plain",
             "body": {"data": base64.urlsafe_b64encode(content.encode()).decode().rstrip("=")}},
        ]},
    }


@pytest.mark.parametrize("words", [
    ["list", "2", "from:reader@example.invalid is:unread"], ["list", "2"],
    ["get", ID], ["get", ID, "--body"],
])
def test_gws_reads_only_list_and_get_and_outputs_compact_json(
    words: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: fake only the external gws process, not the parser/MIME/argv builder.
    calls: list[tuple[list[str], dict[str, gmail.Json]]] = []
    def run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        assert argv[:4] == ["gws", "gmail", "users", "messages"]
        assert argv[4] in {"list", "get"}
        assert argv[5] == "--params"
        assert "shell" not in kwargs
        params = json.loads(argv[6])
        calls.append((argv, params))
        response = {"messages": [{"id": ID}, {"id": OTHER}]} if argv[4] == "list" else _message(params["id"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="not exposed")
    monkeypatch.setattr(gmail.subprocess, "run", run)
    # When
    assert gmail.main(words) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    # Then
    assert result["status"] == "ok"
    assert len(output.out.splitlines()) == 1
    assert output.err == ""
    mails = result["mails"] if words[0] == "list" else [result["mail"]]
    for row in mails:
        assert set(row) == {"id", "from", "subject", "date", "snippet"} | (
            {"body"} if "--body" in words else set())
        assert row["from"] == "reader@example.invalid"
        assert row["subject"] == "제목"
        if "--body" in words:
            assert row["body"] == "안녕\n본문"
    if words[0] == "list":
        assert [call[0][4] for call in calls] == ["list", "get", "get"]
        assert calls[0][1] == {"userId": "me", "maxResults": 2, **({"q": words[2]} if len(words) == 3 else {})}
        assert all(call[1]["format"] == "metadata" for call in calls[1:])
    else:
        assert calls[0][1] == {"userId": "me", "id": ID, "format": "full" if "--body" in words else "metadata"}


def test_html_fallback_and_attachment_exclusion() -> None:
    # Given
    payload = _message(ID, html=True)["payload"]
    assert isinstance(payload, dict)
    # When / Then
    assert gmail._body(payload) == "안녕 & 본문"


@pytest.mark.parametrize("operation", WRITES)
def test_even_the_helper_cannot_build_a_gws_write(operation: str) -> None:
    # Given / When / Then
    with pytest.raises(gmail.GmailReadError):
        gmail.argv(operation, {"userId": "me"})


@pytest.mark.parametrize("operation", WRITES)
def test_generic_command_request_cannot_smuggle_a_gmail_write(operation: str) -> None:
    # Given / When / Then: this is still only a proposal, but Gmail writes are out of scope.
    from tests.unit.test_yoodori_gate import ORIGIN

    encoded = base64.b64encode(json.dumps(
        ["/usr/bin/gws", "gmail", "users", "messages", operation]).encode()).decode()
    with pytest.raises(gate.Refused):
        gate.build(["command-request", "--summary", "s", "--target", "t",
                    "--argv-b64", encoded, *ORIGIN])


@pytest.mark.parametrize("failure", ["rc", "invalid-json", "invalid-message", "missing", "timeout"])
def test_failures_do_not_print_gws_tokens_or_error_output(
    failure: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    secret = "private-value-must-not-appear"
    def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        if failure == "missing":
            raise FileNotFoundError(secret)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(secret, 60)
        return SimpleNamespace(returncode=1 if failure == "rc" else 0,
                               stdout=secret if failure == "invalid-json" else "{}",
                               stderr=secret)
    monkeypatch.setattr(gmail.subprocess, "run", run)
    # When / Then
    assert gmail.main(["get", ID]) == 3
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "error", "error": "gmail-read-failed"}
    assert secret not in output.out + output.err


def test_actual_helper_cli_is_read_only(tmp_path: Path) -> None:
    # Given: a local fake gws, with the actual helper run as a separate process.
    executable = tmp_path / "gws"
    executable.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        "assert sys.argv[1:4] == ['gmail', 'users', 'messages']\n"
        "assert sys.argv[4] == 'list'\n"
        "print(json.dumps({'messages': []}))\n")
    executable.chmod(0o755)
    # When
    result = subprocess.run(
        [sys.executable, gmail.__file__, "list", "2"], env={**gate.ENV, "PATH": str(tmp_path)},
        capture_output=True, text=True, check=False)
    # Then
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "ok", "mails": []}

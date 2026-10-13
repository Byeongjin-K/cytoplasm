"""A failed send says where it stopped, on stdout and in the attempt log (2026-10-13).

The approved forward of that day stopped at the compose step and its record said
only ``browser_command_failed``; ``send-attempts.jsonl`` got no line at all."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mailon import main as mailon_main
from mailon.browser import BrowserError
from mailon.send import SendStepError

_NOT_BUILT = ("agent-browser failed (1): cmd=eval --stdin stderr=Evaluation error: "
              "TypeError: tabPanel._getMenuById is not a function")


def test_cmd_send_failure_is_written_to_the_attempt_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a real send whose compose step fails
    class _Cfg:
        logs_dir = tmp_path / "logs"
        data_dir = tmp_path / "data"

    def failing_send(_args, _cfg) -> int:
        raise SendStepError("compose_open", BrowserError(_NOT_BUILT, command="eval"))

    monkeypatch.setattr(mailon_main, "load_config", lambda: _Cfg)
    monkeypatch.setattr(mailon_main, "_setup_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(mailon_main, "_perform_send", failing_send)
    args = type("Args", (), {"dry_run": False, "confirm_send": True, "json": True})()

    # When: the send command runs
    rc = mailon_main.cmd_send(args)

    # Then: stdout and the attempt log both name the step, cause and command
    assert rc == 2
    printed = json.loads(capsys.readouterr().out)
    assert (printed["error_code"], printed["cause"], printed["step"]) == (
        "external_service_error", "compose_not_ready", "compose_open")
    line = json.loads((_Cfg.logs_dir / "send-attempts.jsonl").read_text().splitlines()[-1])
    assert (line["status"], line["step"], line["browser_command"], line["error_kind"]) == (
        "error", "compose_open", "eval", "js_type_error")
    assert "message" not in line and "tabPanel" not in json.dumps(line)


def test_a_login_failure_names_the_login_step() -> None:
    payload = mailon_main._send_error_payload(mailon_main.LoginError("bad otp"))

    assert (payload["error_code"], payload["cause"], payload["step"]) == (
        "auth_error", "login_failed", "login")

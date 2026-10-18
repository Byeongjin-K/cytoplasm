"""Q245 relay boundary regressions; legacy client tests remain unchanged."""
from __future__ import annotations

import fcntl
import io
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation import codex_llm
from automation.node_config import default_node_config


def relay_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> codex_llm.CodexClient:
    from automation.omo_relay import client

    root = tmp_path / ".hermes/omo-relay"
    root.mkdir(parents=True)
    (root / "mode").write_bytes(b"on")
    config = replace(default_node_config(), omo_relay_target="relay-account@relay.invalid",
                     omo_relay_key=tmp_path / ".ssh/omo_relay")
    monkeypatch.setattr(client, "load_node_config", lambda *_args: config)
    monkeypatch.setattr(client, "record_result", lambda *_args: None)
    return codex_llm.CodexClient.from_environment({"HOME": str(tmp_path)})


def test_enabled_client_uses_stdin_and_actual_served_route(tmp_path, monkeypatch):
    # Given: enabled relay and a provider-reported answer.
    client = relay_client(tmp_path, monkeypatch)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"text":"5","provider":"actual","model":"answerer"}', "")

    monkeypatch.setattr(subprocess, "run", run)
    # When: public API completes.
    answer = client.complete_served("private prompt")
    # Then: only stdin carries the prompt; no model selection is passed.
    assert answer == codex_llm.Served("5", "actual", "answerer")
    argv, kwargs = calls[0]
    assert argv == ["ssh", "-T", "-o", "BatchMode=yes", "-i",
                    str(tmp_path / ".ssh/omo_relay"), "relay-account@relay.invalid"]
    assert kwargs["input"] == "private prompt"
    assert not {"--model", "-m", "--provider", "--usage-file"} & set(argv)


@pytest.mark.parametrize("outcome", [
    subprocess.CompletedProcess([], 255, "", "connect failed"),
    subprocess.CompletedProcess([], 75, "", "busy"),
    subprocess.CompletedProcess([], 1, "", "HTTP 429 Too Many Requests"),
    subprocess.TimeoutExpired([], 1),
    OSError("connect failed"),
])
def test_unavailable_retries_once_then_raises(tmp_path, monkeypatch, outcome):
    from automation.omo_relay import client as relay

    client = relay_client(tmp_path, monkeypatch)
    calls, delays = [], []

    def run(*args, **kwargs):
        calls.append(args)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(relay.time, "sleep", delays.append)
    with pytest.raises(codex_llm.CodexUnavailableError):
        client.complete("prompt")
    assert len(calls) == 2
    assert delays == [30]


def test_empty_answer_is_not_unavailable_and_missing_route_is_unknown(tmp_path, monkeypatch):
    client = relay_client(tmp_path, monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, '{"text":""}', ""))
    with pytest.raises(codex_llm.CodexError) as error:
        client.complete("prompt")
    assert not isinstance(error.value, codex_llm.CodexUnavailableError)


def test_missing_served_fields_are_unknown(tmp_path, monkeypatch):
    client = relay_client(tmp_path, monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, '{"text":"ok"}', ""))
    assert client.complete_served("prompt") == codex_llm.Served("ok", "unknown", "unknown")


def test_success_after_busy_uses_only_one_retry(tmp_path, monkeypatch):
    from automation.omo_relay import client as relay

    client = relay_client(tmp_path, monkeypatch)
    outcomes = iter([subprocess.CompletedProcess([], 75, "", ""),
                     subprocess.CompletedProcess([], 0, '{"text":"ok"}', "")])
    delays = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: next(outcomes))
    monkeypatch.setattr(relay.time, "sleep", delays.append)
    assert client.complete("prompt") == "ok"
    assert delays == [30]


@pytest.mark.parametrize("outcome,expected", [
    (subprocess.TimeoutExpired([], 300), 124),
    (subprocess.CompletedProcess([], 1, "", "HTTP 429"), 75),
    (subprocess.CompletedProcess([], 0, json.dumps({"type": "message_end", "message": {
        "role": "assistant", "stopReason": "error", "errorMessage": "429"}}), ""), 75),
])
def test_runner_timeout_and_overload_return_bounded_error_codes(tmp_path, monkeypatch, outcome, expected):
    from automation.omo_relay import runner

    def run(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(subprocess, "run", run)
    assert runner.run(io.BytesIO(b"prompt"), io.StringIO(), tmp_path) == expected


def test_relay_refuses_oversize_and_busy_without_running_omo(tmp_path, monkeypatch):
    from automation.omo_relay import runner

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    assert runner.run(io.BytesIO(b"x" * (256 * 1024 + 1)), io.StringIO(), tmp_path) == 2
    root = tmp_path / ".dori/omo-relay"
    with (root / "slot-0.lock").open("a") as first, (root / "slot-1.lock").open("a") as second:
        fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert runner.run(io.BytesIO(b"private prompt"), io.StringIO(), tmp_path) == 75


def test_relay_logs_only_metadata_and_uses_empty_private_cwd(tmp_path, monkeypatch):
    from automation.omo_relay import runner

    def run(argv, **kwargs):
        cwd = Path(kwargs["cwd"])
        assert cwd.stat().st_mode & 0o777 == 0o700
        assert list(cwd.iterdir()) == []
        assert kwargs["timeout"] == 300
        assert kwargs["input"] == "private prompt"
        assert not {"--model", "-m"} & set(argv)
        assert {"--no-tools", "--no-extensions", "--no-context-files",
                "--no-skills", "--no-session", "--offline"} <= set(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "type": "agent_end", "messages": [{"role": "assistant", "provider": "actual",
                "model": "answerer", "content": [{"type": "text", "text": "private answer"}]}]
        }) + "\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    output = io.StringIO()
    assert runner.run(io.BytesIO(b"private prompt"), output, tmp_path) == 0
    assert json.loads(output.getvalue())["text"] == "private answer"
    log = tmp_path / ".dori/omo-relay/calls.jsonl"
    raw = log.read_text()
    assert "private prompt" not in raw and "private answer" not in raw
    assert set(json.loads(raw)) == {"at", "caller", "rc", "seconds", "model", "in_bytes", "out_bytes"}
    assert log.stat().st_mode & 0o777 == 0o600

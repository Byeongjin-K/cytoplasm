"""Shared client contract: exact argv (Codex pinned, user config honored), fail-closed
errors, no client-side retry — the only fallback is Hermes' own fallback_providers chain."""

from __future__ import annotations

import inspect
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from automation import codex_llm
from automation.codex_llm import CodexClient, CodexError, CodexUnavailableError

# An absent home, not the literal /home/agent: on the primary node that account exists and
# its omo-relay settings are unreadable to the test user (conftest's unit node root).
HOME = str(Path(os.environ["AUTOPHAGY_UNIT_NODE_ROOT"]) / "home" / "agent")
BIN = f"{HOME}/.local/bin/hermes"
ENV: Mapping[str, str] = {"HOME": HOME, "AUTOPHAGY_HERMES_BIN": BIN}
NO_CREDENTIALS = "hermes -z: agent failed: No Codex credentials stored. Run `hermes auth` to authenticate."  # noqa: E501


class FakeRun:
    """Records every subprocess.run call and replays queued outcomes in order."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), dict(kwargs)))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, subprocess.CompletedProcess)
        return outcome


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> object:
    return subprocess.CompletedProcess(
        args=[BIN], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _install(monkeypatch: pytest.MonkeyPatch, *outcomes: object) -> FakeRun:
    fake = FakeRun(*outcomes)
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def _client(**overrides: object) -> CodexClient:
    return CodexClient.from_environment(ENV, **overrides)  # type: ignore[arg-type]


def test_argv_leaves_the_model_to_the_account_config(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _completed(stdout="ULW-C5-OK\n"))
    _client().complete("ping")
    argv, _ = fake.calls[0]
    assert argv == [BIN, "-z", "ping", "-t", "todo"]


def test_subprocess_invocation_is_sandboxed_and_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install(monkeypatch, _completed(stdout="ok\n"))
    _client().complete("ping")
    _, kwargs = fake.calls[0]
    assert kwargs == {
        "cwd": tempfile.gettempdir(),
        "env": {"HOME": HOME, "PATH": f"{HOME}/.local/bin:/usr/bin:/bin"},
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "check": False,
        "timeout": 180.0,
    }


def test_success_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _completed(stdout="ULW-C5-OK\n"))
    assert _client().complete("ping") == "ULW-C5-OK"


def test_missing_codex_credentials_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _completed(returncode=1, stdout="", stderr=NO_CREDENTIALS))
    with pytest.raises(CodexUnavailableError) as raised:
        _client().complete("ping")
    assert "No Codex credentials stored." in str(raised.value)
    assert len(fake.calls) == 1


def test_empty_stdout_is_a_request_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _completed(stdout="   \n"))
    with pytest.raises(CodexError) as raised:
        _client().complete("ping")
    assert not isinstance(raised.value, CodexUnavailableError)


def test_timeout_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, subprocess.TimeoutExpired(cmd=[BIN], timeout=180.0))
    with pytest.raises(CodexUnavailableError) as raised:
        _client().complete("ping")
    assert "timed out" in str(raised.value)


def test_unexecutable_binary_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, OSError(13, "Permission denied"))
    with pytest.raises(CodexUnavailableError) as raised:
        _client().complete("ping")
    assert str(raised.value) == "Codex binary could not be executed: PermissionError"


def test_stderr_tail_is_redacted_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-" + "A" * 60
    noisy = ("x" * 400) + f" token={secret}"
    _install(monkeypatch, _completed(returncode=2, stderr=noisy))
    with pytest.raises(CodexUnavailableError) as raised:
        _client().complete("ping")
    message = str(raised.value)
    assert secret not in message
    assert "<redacted>" in message
    assert len(message.split(": ", 1)[1]) <= 200


def test_timeout_override_reaches_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _completed(stdout="ok"), _completed(stdout="ok"))
    client = _client(timeout=30.0)
    client.complete("ping")
    client.complete("ping", timeout=5.0)
    assert [call[1]["timeout"] for call in fake.calls] == [30.0, 5.0]


def test_no_environment_or_argument_can_name_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _completed(stdout="ok"), _completed(stdout="ok"))
    CodexClient.from_environment({**ENV, "AUTOPHAGY_CODEX_MODEL": "some-other-model"}).complete("p")
    codex_llm.complete("p", env=ENV)
    for argv, _ in fake.calls:
        assert not {"-m", "--model", "--provider", "--ignore-user-config"} & set(argv)


def test_binary_resolution_prefers_override_then_home_then_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home_binary = tmp_path / ".local" / "bin" / "hermes"
    home_binary.parent.mkdir(parents=True)
    home_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    home_binary.chmod(0o755)
    path_dir = tmp_path / "pathbin"
    path_dir.mkdir()
    path_binary = path_dir / "hermes"
    path_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    path_binary.chmod(0o755)
    base = {"HOME": str(tmp_path), "PATH": str(path_dir)}

    override = CodexClient.from_environment({**base, "AUTOPHAGY_HERMES_BIN": BIN})
    assert override.binary == BIN
    assert CodexClient.from_environment(base).binary == str(home_binary)

    home_binary.unlink()
    assert CodexClient.from_environment(base).binary == str(path_binary)

    path_binary.unlink()
    with pytest.raises(CodexUnavailableError):
        CodexClient.from_environment(base)


def test_missing_home_is_unavailable() -> None:
    with pytest.raises(CodexUnavailableError):
        CodexClient.from_environment({"AUTOPHAGY_HERMES_BIN": BIN})


@pytest.mark.parametrize(
    "outcome",
    [
        _completed(returncode=1, stderr=NO_CREDENTIALS),
        _completed(returncode=429, stderr="rate limited"),
        _completed(stdout=""),
        subprocess.TimeoutExpired(cmd=[BIN], timeout=1.0),
        OSError("boom"),
    ],
)
def test_no_failure_mode_retries_or_switches_provider(
    monkeypatch: pytest.MonkeyPatch, outcome: object
) -> None:
    fake = _install(monkeypatch, outcome, _completed(stdout="SHOULD-NEVER-BE-REACHED"))
    with pytest.raises(CodexError):
        _client().complete("ping")
    assert len(fake.calls) == 1
    assert "--provider" not in fake.calls[0][0]


def test_module_declares_no_other_provider() -> None:
    source = Path(codex_llm.__file__).read_text(encoding="utf-8").lower()
    assert "litellm" not in source
    assert "glm" not in source
    assert '"--provider"' not in source
    assert codex_llm.PROVIDER == "openai-codex"
    assert codex_llm.CONFIGURED_MODEL == "hermes-config"


def test_complete_matches_the_llm_client_protocol_shape() -> None:
    parameters = list(inspect.signature(CodexClient.complete).parameters.values())
    assert [p.name for p in parameters[:2]] == ["self", "prompt"]
    assert parameters[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[1].annotation == "str"
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters[2:])

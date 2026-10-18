"""The peer follows the agent's main model and fallback; drift is reported, never guessed.

Owner decision 2026-09-29: the agent account's ``~/.hermes/config.yaml`` names the one main
model and fallback chain for everything. The peer reads its own file, so it had quietly stayed
on a different route. These cases pin the operator command that keeps the two in step.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from automation import model_sync
from automation.doctor import cli as doctor_cli

AGENT_CONFIG = """model:
  default: gpt-6-sol
  provider: openai-codex
providers:
  litellm: {}
fallback_providers:
  - provider: xai-oauth
    model: grok-4.7
max_concurrent_sessions: 5
"""
PEER_CONFIG = """model:
  provider: custom:litellm
  default: glm-main
_config_version: 37
custom_providers:
  - name: litellm
    base_url: http://127.0.0.1:4000/v1
# peer-only comment stays
plugins:
  enabled: []
"""


class _Node:
    def __init__(self, peer_config: str, peer_logins: str = "openai-codex\nxai-oauth\n") -> None:
        self.files = {"agent": AGENT_CONFIG, "peer": peer_config}
        self.logins = peer_logins
        self.writes: list[str] = []

    def run(self, argv: tuple[str, ...], stdin: str | None) -> tuple[int, str, str]:
        account, script = argv[3], argv[-1]
        if script == model_sync._READ_CONFIG:  # pyright: ignore[reportPrivateUsage]
            return 0, self.files[account], ""
        if script == model_sync._READ_LOGINS:  # pyright: ignore[reportPrivateUsage]
            return 0, self.logins, ""
        assert script == model_sync._WRITE_CONFIG and stdin is not None  # pyright: ignore[reportPrivateUsage]
        self.files[account] = stdin
        self.writes.append(account)
        return 0, "/home/peer/.hermes/config.yaml.bak-model-sync-X\n", ""


def _main(node: _Node, *args: str) -> int:
    return model_sync.main(list(args), run=node.run)


def test_drift_is_reported_without_writing(capsys: pytest.CaptureFixture[str]) -> None:
    node = _Node(PEER_CONFIG)

    assert _main(node) == 1
    assert "MODEL-SYNC drift" in capsys.readouterr().out
    assert node.writes == []


def test_apply_copies_only_the_model_and_fallback_blocks(capsys: pytest.CaptureFixture[str]) -> None:
    node = _Node(PEER_CONFIG)

    assert _main(node, "--apply") == 0

    peer = node.files["peer"]
    assert model_sync.blocks(peer) == model_sync.blocks(AGENT_CONFIG)
    for kept in ("_config_version: 37", "custom_providers:", "# peer-only comment stays", "plugins:"):
        assert kept in peer
    assert "MODEL-SYNC applied" in capsys.readouterr().out
    assert _main(node) == 0


def test_apply_refuses_a_provider_the_peer_cannot_log_in_to(capsys: pytest.CaptureFixture[str]) -> None:
    node = _Node(PEER_CONFIG, peer_logins="openai-codex\n")

    assert _main(node, "--apply") == 2
    assert "MODEL-SYNC-BLOCKED peer 에 xai-oauth 로그인이 없다" in capsys.readouterr().err
    assert node.writes == []


def test_doctor_operator_mode_never_reads_peer_or_runs_model_parity(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    node = _Node(PEER_CONFIG)
    monkeypatch.setattr(doctor_cli, "_config", lambda: None)
    calls = []

    def child(node: _Node) -> Callable[[tuple[str, ...], object], tuple[int, str, str]]:
        def run(argv: tuple[str, ...], _cwd: object) -> tuple[int, str, str]:
            calls.append(argv)
            if argv[-1] in (model_sync._READ_CONFIG, model_sync._READ_LOGINS):  # pyright: ignore[reportPrivateUsage]
                return node.run(argv, None)
            return 1, "", "no doctor here"
        return run

    _ = doctor_cli._operator(doctor_cli._Arguments(), None, child(node))  # pyright: ignore[reportPrivateUsage]
    assert "model-parity" not in capsys.readouterr().out

    node.files["peer"] = model_sync.replace_blocks(PEER_CONFIG, model_sync.blocks(AGENT_CONFIG))
    _ = doctor_cli._operator(doctor_cli._Arguments(), None, child(node))  # pyright: ignore[reportPrivateUsage]
    assert "model-parity" not in capsys.readouterr().out
    assert len(calls) == 2
    assert all(argv[3] == "agent" for argv in calls)
    assert all(argv[-1] not in (model_sync._READ_CONFIG, model_sync._READ_LOGINS) for argv in calls)

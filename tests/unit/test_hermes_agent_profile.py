"""Agent-only composite convergence through the real observer and deploy helper."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from automation import deploy_all_config, deploy_all_observe, hermes_agent_chat_ignore as ignore
from automation import hermes_memory_config as limits
from automation.deploy_declarations import all_declarations
from automation.memory_provider import agent_config as agent
from automation.memory_provider import config as memory

REPO = Path(__file__).resolve().parents[2]
CHANNEL = "123456789012345678"


def _account(tmp_path: Path, text: str) -> Path:
    root = tmp_path / ".hermes"
    (root / "interop").mkdir(parents=True)
    (root / "interop/config.json").write_text(json.dumps({"agent_chat_channel_id": CHANNEL}))
    path = root / "config.yaml"
    path.write_text(text)
    return path


def test_composite_applies_both_selections_with_one_backup(tmp_path: Path) -> None:
    # Given
    before = "memory:\n  nudge_interval: 10\ndiscord:\n  ignored_channels: '44'\nother: unchanged\n"
    path = _account(tmp_path, before)
    # When
    codes = [agent.main([mode], home=tmp_path) for mode in ("--check", "--apply", "--check", "--apply")]
    # Then
    assert codes == [1, 0, 0, 0]
    assert memory.read_profile(path.read_text()).identity() == memory.wanted_identity()
    assert ignore.read_ignored(path.read_text()).channels == ("44", CHANNEL)
    assert yaml.safe_load(path.read_text())["memory"]["nudge_interval"] == 10
    assert path.read_text().endswith("other: unchanged\n")
    assert [p.read_text() for p in path.parent.glob("*.bak-*")] == [before]
    assert re.fullmatch(r"[a-z_]+=[a-z0-9_-]+(?:,[a-z_]+=[a-z0-9_-]+)*", agent.wanted_identity())


@pytest.mark.parametrize("memory_ok, ignored_ok, status", [
    (False, False, "stale"), (False, True, "stale"), (True, False, "stale"), (True, True, "ok"),
])
def test_real_observer_detects_either_drift(
    tmp_path: Path, memory_ok: bool, ignored_ok: bool, status: str,
) -> None:
    # Given: real account-side --show via the release observer's closed script.
    text = "discord:\n  ignored_channels: []\n"
    if memory_ok:
        text = memory.apply_profile(text)
    if ignored_ok:
        text = ignore.apply_ignore(text, CHANNEL)
    _account(tmp_path, text)
    decl = next(row for row in all_declarations(REPO)
                if row.account == "agent" and row.destination == ".hermes/config.yaml")

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        result = subprocess.run(
            ["bash", "-c", deploy_all_observe.REMOTE_SCRIPT, "_", *args],
            env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=10, check=False,
        )
        return result.returncode, result.stdout

    # When
    facts = deploy_all_config.observe_config(decl, REPO, runner)
    # Then
    assert facts[2] == status
    assert (("different-agent_chat_ignored", "-") in facts[3]) == (not ignored_ok)
    assert agent.main(["--check"], home=tmp_path) == (0 if status == "ok" else 1)


def test_release_row_changes_only_agent_and_keeps_single_owner() -> None:
    # Given / When
    rows = [row for row in all_declarations(REPO) if row.destination == ".hermes/config.yaml"]
    # Then
    assert len(rows) == 2
    assert {(row.account, row.attr("algorithm"), row.attr("activation"), row.owner) for row in rows} == {
        ("agent", agent.ALGORITHM, "gateway", "automation/memory_curator"),
        ("peer", memory.ALGORITHM, "gateway", "automation/memory_curator"),
    }


def test_shared_helper_runs_new_apply_and_check(tmp_path: Path) -> None:
    # Given
    path = _account(tmp_path, "discord:\n  ignored_channels: []\n")
    script = (
        'source "$1"; run_account() { bash -c "$2"; }; '
        f"converge_config agent .hermes/config.yaml {agent.ALGORITHM}"
    )
    # When
    result = subprocess.run(
        ["bash", "-c", script, "_", str(REPO / "automation/deploy_config.sh")],
        env={**os.environ, "HOME": str(tmp_path), "NODE_RELEASE_CURRENT": str(REPO)},
        capture_output=True, text=True, timeout=10, check=False,
    )
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert agent.identity(path.read_text(), CHANNEL) == agent.wanted_identity()
    assert agent.MARKER in result.stdout
    assert len(list(path.parent.glob("*.bak-*"))) == 1


@pytest.mark.parametrize("part", ["memory", "ignore"])
def test_composite_readback_checks_both_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, part: str,
) -> None:
    # Given
    path = _account(tmp_path, "discord:\n  ignored_channels: []\n")
    partial = memory.apply_profile("") if part == "memory" else ignore.apply_ignore("", CHANNEL)

    def partial_write(path: Path, text: str) -> Path:
        path.write_text(partial)
        return path

    monkeypatch.setattr(ignore, "write_config", partial_write)
    # When
    code = agent.main(["--apply"], home=tmp_path)
    # Then
    assert code == 5
    assert path.read_text() == partial


@pytest.mark.parametrize("text, code", [
    ("memory:\n  provider: synthetic_other\n", 7),
    ("memory:\n  provider: builtin\ndiscord: {}\n", 3),
    ("memory:\n  provider:\n    name: other\n", 3),
])
def test_composite_refuses_before_any_write(tmp_path: Path, text: str, code: int) -> None:
    # Given
    path = _account(tmp_path, text)
    # When
    result = agent.main(["--apply"], home=tmp_path)
    # Then
    assert result == code
    assert path.read_text() == text
    assert not list(path.parent.glob("*.bak-*"))


def test_composite_keeps_lowering_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    text = memory.apply_profile("")
    path = _account(tmp_path, text)
    (path.parent / "memories").mkdir()
    (path.parent / "memories/USER.md").write_text("x" * 2500)
    monkeypatch.setattr(limits, "LIMITS", {"memory_char_limit": 3300, "user_char_limit": 2000})
    # When
    code = agent.main(["--apply"], home=tmp_path)
    # Then
    assert code == 7
    assert path.read_text() == text
    assert not list(path.parent.glob("*.bak-*"))


@pytest.mark.parametrize("mode", ["--show", "--check", "--apply"])
def test_composite_requires_node_channel_before_writing(tmp_path: Path, mode: str) -> None:
    # Given
    before = "memory:\n  nudge_interval: 10\n"
    path = _account(tmp_path, before)
    (path.parent / "interop/config.json").unlink()
    # When
    code = agent.main([mode], home=tmp_path)
    # Then
    assert code == 3
    assert path.read_text() == before
    assert not list(path.parent.glob("*.bak-*"))

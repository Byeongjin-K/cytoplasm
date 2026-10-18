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


def test_absent_switch_wants_memory_only() -> None:
    # Given / When / Then: release-host HOME is isolated by conftest.
    assert agent.wanted_identity() == f"{memory.wanted_identity()},agent_chat_ignored=no"


def test_composite_applies_both_selections_with_one_backup(tmp_path: Path) -> None:
    # Given
    before = "memory:\n  nudge_interval: 10\ndiscord:\n  ignored_channels: '44'\nother: unchanged\n"
    path = _account(tmp_path, before)
    # When
    codes = [agent.main([mode, "--dori-agent-chat", "on"], home=tmp_path)
             for mode in ("--check", "--apply", "--check", "--apply")]
    # Then
    assert codes == [1, 0, 0, 0]
    assert memory.read_profile(path.read_text()).identity() == memory.wanted_identity()
    assert ignore.read_ignored(path.read_text()).channels == ("44", CHANNEL)
    assert yaml.safe_load(path.read_text())["memory"]["nudge_interval"] == 10
    assert path.read_text().endswith("other: unchanged\n")
    assert [p.read_text() for p in path.parent.glob("*.bak-*")] == [before]
    assert re.fullmatch(r"[a-z_]+=[a-z0-9_-]+(?:,[a-z_]+=[a-z0-9_-]+)*",
                        agent.wanted_identity(dori_agent_chat=True))


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("memory_ok, ignored_ok, status", [
    (False, False, "stale"), (False, True, "stale"), (True, False, "stale"), (True, True, "ok"),
])
def test_real_observer_detects_either_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    memory_ok: bool, ignored_ok: bool, status: str, enabled: bool,
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
    host = tmp_path / "host"
    switch = host / ".hermes/release-flags/dori-agent-chat"
    if enabled:
        switch.parent.mkdir(parents=True)
        switch.write_text("555")
    monkeypatch.setenv("HOME", str(host))

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        assert args[-2:] == ("--dori-agent-chat", "on" if enabled else "off")
        result = subprocess.run(
            ["bash", "-c", deploy_all_observe.REMOTE_SCRIPT, "_", *args],
            env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=10, check=False,
        )
        return result.returncode, result.stdout

    # When
    facts = deploy_all_config.observe_config(decl, REPO, runner)
    # Then
    expected = status if enabled else ("ok" if memory_ok and not ignored_ok else "stale")
    assert facts[2] == expected
    assert (("different-agent_chat_ignored", "-") in facts[3]) == (ignored_ok != enabled)
    assert agent.main(["--check", "--dori-agent-chat", "on" if enabled else "off"],
                      home=tmp_path) == (0 if expected == "ok" else 1)


def test_release_row_changes_only_agent_and_keeps_single_owner() -> None:
    # Given / When
    rows = [row for row in all_declarations(REPO) if row.destination == ".hermes/config.yaml"]
    # Then
    assert len(rows) == 2
    assert {(row.account, row.attr("algorithm"), row.attr("activation"), row.owner) for row in rows} == {
        ("agent", agent.ALGORITHM, "gateway", "automation/memory_curator"),
        ("peer", memory.ALGORITHM, "gateway", "automation/memory_curator"),
    }


def test_observer_refuses_invalid_host_switch_without_a_node_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    switch = tmp_path / ".hermes/release-flags/dori-agent-chat"
    switch.parent.mkdir(parents=True)
    switch.write_text("invalid")
    monkeypatch.setenv("HOME", str(tmp_path))
    decl = next(row for row in all_declarations(REPO)
                if row.account == "agent" and row.destination == ".hermes/config.yaml")

    def no_transport(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        pytest.fail("invalid switch must refuse before transport")

    # When
    facts = deploy_all_config.observe_config(decl, REPO, no_transport)
    # Then
    assert facts == ("?", "?", "unknown", (("invalid-release-switch", "-"),))


def test_shared_helper_runs_new_apply_and_check(tmp_path: Path) -> None:
    # Given
    path = _account(tmp_path, "discord:\n  ignored_channels: []\n")
    script = (
        'source "$1"; run_account() { bash -c "$2"; }; '
        f"converge_config agent .hermes/config.yaml {agent.ALGORITHM} --dori-agent-chat on"
    )
    # When
    result = subprocess.run(
        ["bash", "-c", script, "_", str(REPO / "automation/deploy_config.sh")],
        env={**os.environ, "HOME": str(tmp_path), "NODE_RELEASE_CURRENT": str(REPO)},
        capture_output=True, text=True, timeout=10, check=False,
    )
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert agent.identity(path.read_text(), CHANNEL) == agent.wanted_identity(dori_agent_chat=True)
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
    code = agent.main(["--apply", "--dori-agent-chat", "on"], home=tmp_path)
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
    result = agent.main(["--apply", "--dori-agent-chat", "on"], home=tmp_path)
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
    code = agent.main(["--apply", "--dori-agent-chat", "on"], home=tmp_path)
    # Then
    assert code == 7
    assert path.read_text() == text
    assert not list(path.parent.glob("*.bak-*"))


@pytest.mark.parametrize("mode", ["--check", "--apply"])
def test_composite_requires_node_channel_before_writing(tmp_path: Path, mode: str) -> None:
    # Given
    before = "memory:\n  nudge_interval: 10\n"
    path = _account(tmp_path, before)
    (path.parent / "interop/config.json").unlink()
    # When
    code = agent.main([mode, "--dori-agent-chat", "on"], home=tmp_path)
    # Then
    assert code == 3
    assert path.read_text() == before
    assert not list(path.parent.glob("*.bak-*"))


@pytest.mark.parametrize("channel_present", [False, True])
@pytest.mark.parametrize("before", [
    "model:\n  provider: synthetic\n",
    "memory:\n  nudge_interval: 10\ndiscord:\n  ignored_channels: '44' # retain\n",
])
def test_off_matches_existing_memory_profile_bytes(
    tmp_path: Path, channel_present: bool, before: str,
) -> None:
    # Given: the existing memory CLI is the golden, not another copy of its editor.
    path = _account(tmp_path, before)
    golden_home = tmp_path / "golden"
    golden_path = _account(golden_home, before)
    assert memory.main(["--apply"], home=golden_home) == 0
    if not channel_present:
        (path.parent / "interop/config.json").unlink()
    # When
    code = agent.main(["--apply", "--dori-agent-chat", "off"], home=tmp_path)
    # Then
    assert code == 0
    assert path.read_bytes() == golden_path.read_bytes()
    assert agent.identity(path.read_text(), CHANNEL if channel_present else None) == agent.wanted_identity(home=tmp_path)


def test_off_removes_an_existing_ignore_without_touching_other_channels(tmp_path: Path) -> None:
    # Given
    before = memory.apply_profile(f"discord:\n  ignored_channels: '44,{CHANNEL}' # keep\n")
    path = _account(tmp_path, before)
    # When
    code = agent.main(["--apply", "--dori-agent-chat", "off"], home=tmp_path)
    # Then
    assert code == 0
    assert path.read_text() == before.replace(f"44,{CHANNEL}", "44")
    assert agent.main(["--check", "--dori-agent-chat", "off"], home=tmp_path) == 0


@pytest.mark.parametrize("raw", [None, "{}", '{"agent_chat_channel_id":"bad"}'])
def test_show_reports_no_when_channel_is_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: str | None,
) -> None:
    # Given
    path = _account(tmp_path, memory.apply_profile(""))
    interop = path.parent / "interop/config.json"
    interop.unlink()
    if raw is not None:
        interop.write_text(raw)
    # When
    code = agent.main(["--show"], home=tmp_path)
    # Then
    assert code == 0
    assert f"have={memory.wanted_identity()},agent_chat_ignored=no" in capsys.readouterr().out


def test_node_never_reads_its_own_release_flags(tmp_path: Path) -> None:
    # Given: a malformed node-side flag must be irrelevant to explicit off.
    path = _account(tmp_path, "discord:\n  ignored_channels: []\n")
    flag = path.parent / "release-flags/dori-agent-chat"
    flag.parent.mkdir()
    flag.write_text("malformed")
    # When
    code = agent.main(["--apply", "--dori-agent-chat", "off"], home=tmp_path)
    # Then
    assert code == 0
    assert ignore.read_ignored(path.read_text()).channels == ()


@pytest.mark.parametrize("mode", ["--check", "--apply"])
def test_node_requires_an_explicit_mode(tmp_path: Path, mode: str) -> None:
    # Given
    path = _account(tmp_path, "model: synthetic\n")
    # When / Then
    with pytest.raises(SystemExit) as refused:
        agent.main([mode], home=tmp_path)
    assert refused.value.code == 2
    assert path.read_text() == "model: synthetic\n"

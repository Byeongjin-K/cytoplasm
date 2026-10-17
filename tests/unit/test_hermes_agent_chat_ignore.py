"""List-only cutover and rollback, independently parsed with Hermes's YAML parser."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from automation import hermes_agent_chat_ignore as ignore
from automation.hermes_memory_config import ConfigShapeError
from automation.interop import free_response

CHANNEL = "123456789012345678"
REST = "# retain\nmodel:\n  provider: synthetic\ntimezone: Asia/Seoul"


@pytest.mark.parametrize("value", [
    "", "''", '""', "[]", "'11,44'", '"11,44"', "11,44",
    "[11, '44']", "\n    - '11'\n    # keep\n    - 44",
    "\n  - '11'\n  - 44",
])
def test_edits_only_the_list_and_is_idempotent(value: str) -> None:
    # Given
    before = f"discord:\n  ignored_channels: {value}\n  free_response_channels: '44'\n" + REST
    # When
    after = ignore.apply_ignore(before, CHANNEL)
    # Then
    original, parsed = yaml.safe_load(before), yaml.safe_load(after)
    original["discord"].pop("ignored_channels")
    assert tuple(free_response.configured_channels(after)[1]) == (*ignore.read_ignored(before).channels, CHANNEL)
    parsed["discord"].pop("ignored_channels")
    assert parsed == original
    assert after.endswith(REST)
    assert ignore.apply_ignore(after, CHANNEL) == after


@pytest.mark.parametrize("before, after", [
    ("model: x", f"model: x\ndiscord:\n  ignored_channels: '{CHANNEL}'\n"),
    ("discord:", f"discord:\n  ignored_channels: '{CHANNEL}'\n"),
    ("discord:\n  nested:\n    ignored_channels: '44'\n",
     f"discord:\n  ignored_channels: '{CHANNEL}'\n  nested:\n    ignored_channels: '44'\n"),
    ("discord:\r\n  ignored_channels: '44' # keep\r\nother: x",
     f"discord:\r\n  ignored_channels: '44,{CHANNEL}' # keep\r\nother: x"),
])
def test_unrelated_bytes_survive_missing_keys_and_crlf(before: str, after: str) -> None:
    # Given / When / Then
    assert ignore.apply_ignore(before, CHANNEL) == after


def test_unrelated_indentless_sequences_survive_before_and_after_discord() -> None:
    # Given: PyYAML's default sequence indentation, also used in account configs.
    prefix = "fallback_providers:\n- provider: synthetic\n  model: x\n"
    suffix = "plugins:\n- name: keep\n  enabled: true\n"
    before = prefix + "discord:\n  ignored_channels: '44'\n" + suffix
    # When
    after = ignore.apply_ignore(before, CHANNEL)
    # Then
    assert after == prefix + f"discord:\n  ignored_channels: '44,{CHANNEL}'\n" + suffix
    assert yaml.safe_load(after)["fallback_providers"] == yaml.safe_load(before)["fallback_providers"]
    assert yaml.safe_load(after)["plugins"] == yaml.safe_load(before)["plugins"]


@pytest.mark.parametrize("text", [
    "discord: {}\n",
    "discord:\n  ignored_channels: [44,\n    55]\n",
    "discord:\n  ignored_channels: [word]\n",
    "discord:\n  ignored_channels: *channels\n",
    "discord:\n  ignored_channels: &channels []\n",
    "discord:\n  ignored_channels: |\n    44\n",
    "discord:\n  ignored_channels: 44\n    - 55\n",
    "discord:\n  ignored_channels:\n    - 44\n      - 55\n",
    "discord:\n  ignored_channels:\n - 44\n",
    "discord:\n  - ignored_channels: []\n",
    "discord:\n  ignored_channels:\n    - {channel: 44}\n",
    "discord:\n  ignored_channels:\n    key: 44\n",
    "discord:\n  ignored_channels: null\n",
    "discord:\n  ignored_channels: true\n",
    "discord:\n  ignored_channels: '11,0'\n",
    "discord:\n  ignored_channels: '11,-44'\n",
    "discord:\n  ignored_channels: []\n  ignored_channels: []\n",
    "discord:\n  ignored_channels: []\n  'ignored_channels': []\n",
    "discord:\n  ignored_channels: []\ndiscord:\n  ignored_channels: []\n",
    "discord:\n  <<: *defaults\n",
    "<<: *defaults\n",
    "---\ndiscord:\n  ignored_channels: []\n",
    "discord:\n\tignored_channels: []\n",
])
def test_ambiguous_shapes_are_refused(text: str) -> None:
    # Given / When / Then
    with pytest.raises(ConfigShapeError):
        ignore.apply_ignore(text, CHANNEL)


def _account(tmp_path: Path, text: str) -> Path:
    root = tmp_path / ".hermes"
    (root / "interop").mkdir(parents=True)
    (root / "interop/config.json").write_text(json.dumps({"agent_chat_channel_id": CHANNEL}))
    path = root / "config.yaml"
    path.write_bytes(text.encode())
    return path


def test_cli_backs_up_once_and_observes_then_removes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Given
    original = "discord:\r\n  ignored_channels: '44' # keep\r\n" + REST
    path = _account(tmp_path, original)
    # When
    codes = [ignore.main([mode], home=tmp_path) for mode in ("--show", "--check", "--apply", "--apply", "--check")]
    # Then
    assert codes == [0, 1, 0, 0, 0]
    assert f"{ignore.MARKER} have=agent_chat_ignored=no" in capsys.readouterr().out
    backups = list(path.parent.glob("config.yaml.bak-agent-profile-*"))
    assert [p.read_bytes() for p in backups] == [original.encode()]
    assert path.read_bytes() == ignore.apply_ignore(original, CHANNEL).encode()
    assert path.stat().st_mode & 0o777 == 0o600
    assert ignore.main(["--remove"], home=tmp_path) == 0
    assert path.read_bytes() == original.encode()
    assert ignore.main(["--remove"], home=tmp_path) == 0
    assert len(list(path.parent.glob("config.yaml.bak-agent-profile-*"))) == 2


@pytest.mark.parametrize("value", [
    f"'{CHANNEL},44,{CHANNEL}'", f"\n    - '{CHANNEL}'\n    - 44\n    - {CHANNEL}",
    f"['{CHANNEL}', 44, '{CHANNEL}']",
])
def test_remove_preserves_other_channels_and_comments(value: str) -> None:
    # Given
    before = f"discord:\n  ignored_channels: {value}\n  # keep\n  reactions: true\n"
    # When
    after = ignore.apply_ignore(before, CHANNEL, remove=True)
    # Then
    assert ignore.read_ignored(after).channels == ("44",)
    assert after.endswith("  # keep\n  reactions: true\n")
    assert ignore.apply_ignore(after, CHANNEL, remove=True) == after


@pytest.mark.parametrize("raw", [None, "{}", "[]", "{broken", '{"agent_chat_channel_id":true}',
                                  '{"agent_chat_channel_id":0}', '{"agent_chat_channel_id":"bad"}'])
@pytest.mark.parametrize("mode", ["--show", "--check", "--apply", "--remove"])
def test_bad_or_missing_channel_never_writes(tmp_path: Path, raw: str | None, mode: str) -> None:
    # Given
    before = "discord:\n  ignored_channels: []\n"
    path = _account(tmp_path, before)
    interop = path.parent / "interop/config.json"
    interop.unlink()
    if raw is not None:
        interop.write_text(raw)
    # When
    code = ignore.main([mode], home=tmp_path)
    # Then
    assert code == 3
    assert path.read_text() == before
    assert not list(path.parent.glob("*.bak-*"))


def test_readback_failure_is_not_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    before = "discord:\n  ignored_channels: []\n"
    path = _account(tmp_path, before)
    monkeypatch.setattr(ignore, "write_config", lambda path, text: path)
    # When
    code = ignore.main(["--apply"], home=tmp_path)
    # Then
    assert code == 5
    assert path.read_text() == before


def test_failed_rename_leaves_original_and_no_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    before = "discord:\n  ignored_channels: []\n"
    path = _account(tmp_path, before)

    def fail_replace(source: str, destination: Path) -> None:
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(ignore.os, "replace", fail_replace)
    # When
    code = ignore.main(["--apply"], home=tmp_path)
    # Then
    assert code == 3
    assert path.read_text() == before
    assert not list(path.parent.glob(".config.yaml.*"))
    assert [p.read_text() for p in path.parent.glob("*.bak-*")] == [before]


@pytest.mark.parametrize("free", ["44", CHANNEL])
def test_gateway_start_does_not_undo_cutover(tmp_path: Path, free: str) -> None:
    # Given: even a stale free-response entry must not undo an ignore.
    before = f"discord:\n  free_response_channels: '{free}'\n  ignored_channels: '{CHANNEL}'\n"
    path = _account(tmp_path, before)
    # When
    code, _ = free_response.run(config=path, interop=path.parent / "interop/config.json", apply=True)
    # Then
    assert code == 0
    assert path.read_text() == before
    assert not list(path.parent.glob("*.bak-*"))

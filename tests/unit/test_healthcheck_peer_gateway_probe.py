"""Read-only peer gateway checks use real files and the sourced Bash surface."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_PROBE: Final = _REPO / "automation/peer_gateway_probe.sh"
_HEALTHCHECK: Final = _REPO / "automation/healthcheck.sh"
_APPROVALS: Final = "12345"


def write_peer_files(tmp_path: Path) -> tuple[Path, Path]:
    """Create real inputs for both probe tests and the complete healthcheck sweep."""
    config = tmp_path / "config.yaml"
    config.write_text(f'discord:\n  ignored_channels: "{_APPROVALS}"\n', encoding="utf-8")
    directory = tmp_path / "channel_directory.json"
    directory.write_text(json.dumps({"platforms": {"discord": [
        {"name": "approvals", "id": _APPROVALS, "type": "text", "guild": "fixture"},
        {"name": "agent-chat", "id": "67890", "type": "text", "guild": "fixture"},
    ]}}), encoding="utf-8")
    return config, directory


@pytest.fixture
def peer_files(tmp_path: Path) -> tuple[Path, Path]:
    return write_peer_files(tmp_path)


def _probe(files: tuple[Path, Path], script: str = 'source "$1"; probe_peer_ignored_channels') -> subprocess.CompletedProcess[str]:
    config, directory = files
    return subprocess.run(
        ["bash", "-c", script, "bash", str(_PROBE), str(_HEALTHCHECK)],
        env={**os.environ,
             "HEALTHCHECK_NODE_CONFIG_PATH": str(_REPO / "configs/node.example.toml"),
             "HEALTHCHECK_PEER_GATEWAY_CONFIG": str(config),
             "HEALTHCHECK_PEER_CHANNEL_DIRECTORY": str(directory),
             "HEALTHCHECK_PEER_IGNORED_MEMO": str(config.parent / "peer-ignored-ids.json")},
        capture_output=True, text=True, check=False, timeout=15,
    )


@pytest.mark.parametrize("ignored", [
    f'"{_APPROVALS}"', f"'{_APPROVALS}'", f'"67890, {_APPROVALS}"',
    f'\n    - "67890"\n    - "{_APPROVALS}"', f'["67890", "{_APPROVALS}"]',
    f'\n  - "{_APPROVALS}"', f'\n    - {_APPROVALS}',
])
def test_passes_when_top_level_discord_ignores_approvals(
    peer_files: tuple[Path, Path], ignored: str,
) -> None:
    # Given: supported scalar/list shapes, distinct from the unrelated nested setting.
    peer_files[0].write_text(
        f'display:\n  platforms:\n    discord:\n      ignored_channels: "67890"\n'
        f'discord:\n  ignored_channels: {ignored}\n', encoding="utf-8",
    )
    # When: the real sourced probe reads both files.
    result = _probe(peer_files)
    # Then: the approvals exclusion is established without emitting IDs or config.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-PASS" in result.stdout
    assert _APPROVALS not in result.stdout + result.stderr


@pytest.mark.parametrize("config", [
    'model: fixture\n', 'discord: {}\n', 'discord:\n  ignored_channels: []\n',
    'discord:\n  ignored_channels: "9123456"\n',
    'discord:\n  ignored_channels: ["67890, 12345"]\n',
    'display:\n  platforms:\n    discord:\n      ignored_channels: "12345"\n',
    'discord:\n  ignored_channels: "12345"\ndiscord: {}\n',
    'discord: {}\ndiscord:\n  ignored_channels: "12345"\n',
    'discord:\n  ignored_channels: "12345"\n  ignored_channels: []\n',
    'discord:\n  ignored_channels: ["12345"\n',
    'discord:\n  ignored_channels: {channel: "12345"}\n',
    'discord:\n  ignored_channels: [true, "12345"]\n',
    'discord:\n  ignored_channels: "12345"\nunrelated: [\n',
    '[]\n', '',
])
def test_fails_when_config_is_absent_ambiguous_or_invalid(
    peer_files: tuple[Path, Path], config: str,
) -> None:
    # Given: exclusion is missing, nested-only, ambiguous, or the YAML is invalid.
    peer_files[0].write_text(config, encoding="utf-8")
    # When
    result = _probe(peer_files)
    # Then
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-RECOVERY:" in result.stdout
    assert "PEER-IGNORED-CHANNELS-PASS" not in result.stdout


_NOTIFICATIONS: Final = "55555"


def _with_notifications(files: tuple[Path, Path]) -> None:
    """The peer bot can see #notifications — the surface it flooded on 2026-09-21."""
    files[1].write_text(json.dumps({"platforms": {"discord": [
        {"name": "approvals", "id": _APPROVALS, "type": "text", "guild": "fixture"},
        {"name": "notifications", "id": _NOTIFICATIONS, "type": "text", "guild": "fixture"},
        {"name": "agent-chat", "id": "67890", "type": "text", "guild": "fixture"},
    ]}}), encoding="utf-8")


def test_fails_when_notifications_is_visible_but_not_ignored(
    peer_files: tuple[Path, Path],
) -> None:
    # Given: the peer directory lists #notifications, but only approvals is ignored —
    # the 2026-09-21 state in which the peer opened a thread on every weekly-report chunk.
    _with_notifications(peer_files)
    # When
    result = _probe(peer_files)
    # Then: drift, with recovery guidance and without leaking the channel id.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-MISSING" in result.stdout
    assert "PEER-IGNORED-CHANNELS-RECOVERY:" in result.stdout
    assert _NOTIFICATIONS not in result.stdout + result.stderr


@pytest.mark.parametrize("ignored", [
    f'"{_APPROVALS}, {_NOTIFICATIONS}"', f'\n    - "{_NOTIFICATIONS}"\n    - {_APPROVALS}',
])
def test_passes_when_both_approvals_and_notifications_are_ignored(
    peer_files: tuple[Path, Path], ignored: str,
) -> None:
    # Given
    _with_notifications(peer_files)
    peer_files[0].write_text(f"discord:\n  ignored_channels: {ignored}\n", encoding="utf-8")
    # When
    result = _probe(peer_files)
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-PASS" in result.stdout
    assert _NOTIFICATIONS not in result.stdout + result.stderr


def test_fails_when_notifications_is_ambiguous(peer_files: tuple[Path, Path]) -> None:
    # Given: two channels named notifications — the probe cannot know which one to require.
    peer_files[1].write_text(json.dumps({"platforms": {"discord": [
        {"name": "approvals", "id": _APPROVALS},
        {"name": "notifications", "id": _NOTIFICATIONS},
        {"name": "notifications", "id": "55556"},
    ]}}), encoding="utf-8")
    peer_files[0].write_text(
        f'discord:\n  ignored_channels: "{_APPROVALS}, {_NOTIFICATIONS}, 55556"\n', encoding="utf-8",
    )
    # When
    result = _probe(peer_files)
    # Then: fail-closed, never a presumed pass.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-INVALID" in result.stdout


@pytest.mark.parametrize("directory", [
    '{', '{}', '[]', '{"platforms":{"discord":[]}}',
    '{"platforms":{"discord":[{"name":"approvals","id":""}]}}',
    '{"platforms":{"discord":[{"name":"approvals","id":"12345"},'
    '{"name":"approvals","id":"67890"}]}}',
    '{"platforms":{"discord":[{"name":"approvals","id":true}]}}',
    '{"platforms":{"discord":[{"name":"approvals","id":"12345"},null]}}',
])
def test_fails_when_directory_cannot_resolve_a_unique_approvals_id(
    peer_files: tuple[Path, Path], directory: str,
) -> None:
    # Given: the authoritative channel directory cannot be parsed or resolved.
    peer_files[1].write_text(directory, encoding="utf-8")
    # When
    result = _probe(peer_files)
    # Then
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-RECOVERY:" in result.stdout


@pytest.mark.parametrize("index", [0, 1])
def test_fails_closed_when_an_input_cannot_be_read(
    peer_files: tuple[Path, Path], index: int,
) -> None:
    # Given: a directory in place of an input fails even when tests run as root.
    peer_files[index].unlink()
    peer_files[index].mkdir()
    # When: sudo fallback is denied deterministically, without invoking host sudo.
    result = _probe(peer_files, 'sudo() { return 1; }; source "$1"; probe_peer_ignored_channels')
    # Then: unknown state is a failure, never a skip or presumed drift.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-UNREADABLE" in result.stdout
    assert "PEER-IGNORED-CHANNELS-RECOVERY:" in result.stdout


def test_dispatches_locally_when_registered(peer_files: tuple[Path, Path]) -> None:
    # Given: real healthcheck definitions and valid peer files.
    script = '''source "$2"
for definition in "${LIVE_CHECKS[@]}"; do
  IFS='|' read -r name kind node account target <<< "$definition"
  if [[ "$kind" == peer_ignored_channels ]]; then
    [[ " $LOCAL_PROBES " == *" $kind "* ]] || exit 2
    run_check "$definition"
    exit $?
  fi
done
exit 3
'''
    # When: the production dispatcher selects the new local probe.
    result = _probe(peer_files, script)
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-PASS" in result.stdout


def _rename_approvals(directory: Path, name: str) -> None:
    rows = json.loads(directory.read_text(encoding="utf-8"))
    for row in rows["platforms"]["discord"]:
        if row["id"] == _APPROVALS:
            row["name"] = name
    directory.write_text(json.dumps(rows), encoding="utf-8")


def test_a_renamed_approvals_channel_stays_required_by_its_remembered_id(
    peer_files: tuple[Path, Path],
) -> None:
    # Given: one pass matched #approvals by name, then the owner renamed it in Discord.
    assert _probe(peer_files).returncode == 0
    memo = peer_files[0].parent / "peer-ignored-ids.json"
    assert oct(memo.stat().st_mode & 0o777) == "0o600"
    _rename_approvals(peer_files[1], "release-approvals")
    # When: the peer still ignores that id
    kept = _probe(peer_files)
    # and when the id is later removed from the peer's ignore list
    peer_files[0].write_text('discord:\n  ignored_channels: "67890"\n', encoding="utf-8")
    dropped = _probe(peer_files)
    # Then: the rename passes, and the id is still enforced without the old name.
    assert kept.returncode == 0, kept.stdout + kept.stderr
    assert "PEER-IGNORED-CHANNELS-PASS" in kept.stdout
    assert dropped.returncode == 1, dropped.stdout + dropped.stderr
    assert "PEER-IGNORED-CHANNELS-MISSING" in dropped.stdout
    assert _APPROVALS not in kept.stdout + dropped.stdout


def test_a_renamed_channel_without_an_earlier_match_fails_closed(
    peer_files: tuple[Path, Path],
) -> None:
    # Given: no earlier pass remembered the id, so nothing identifies the approvals channel.
    _rename_approvals(peer_files[1], "release-approvals")
    # When
    result = _probe(peer_files)
    # Then
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-INVALID" in result.stdout


def test_a_remembered_id_the_directory_no_longer_lists_is_not_required(
    peer_files: tuple[Path, Path],
) -> None:
    # Given: an earlier pass remembered an id whose channel has since been replaced.
    (peer_files[0].parent / "peer-ignored-ids.json").write_text(
        json.dumps({"approvals": ["99999"], "notifications": []}), encoding="utf-8",
    )
    # When
    result = _probe(peer_files)
    # Then: only channels the peer can still see are required.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PEER-IGNORED-CHANNELS-PASS" in result.stdout

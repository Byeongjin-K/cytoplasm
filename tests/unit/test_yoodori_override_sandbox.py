"""Derive override visibility from the actual repair unit sandboxes, not a copied assumption."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from automation import node_config, yoodori_channel
from automation.interop import approval_directory
from tests.unit.test_agent_chat_channel_move import NEW, OLD

REPO = Path(__file__).resolve().parents[2]


def _hidden_roots() -> tuple[Path, ...]:
    units = REPO / "automation/repair/systemd"
    protecting = [
        unit for unit in units.glob("*.service")
        if re.search(r"^ProtectHome=(yes|true|on|1|tmpfs)$", unit.read_text(), re.MULTILINE)
    ]
    assert {unit.name for unit in protecting} >= {
        "autophagy-repair-agent.service", "autophagy-repair-approval-watch.service",
    }
    return tuple(Path(root) for root in ("/home", "/root", "/run/user"))


def _visible(path: Path, ops_home: Path) -> None:
    assert not any(path.is_relative_to(hidden) for hidden in (*_hidden_roots(), ops_home)), (
        f"{path} is hidden by the repair units' ProtectHome setting"
    )


def test_release_override_writer_uses_configured_private_root_not_ops_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the node's root-readable config is outside the home the units hide.
    home, private = tmp_path / "home/ops", tmp_path / "private"
    home.mkdir(parents=True)
    private.mkdir(mode=0o700)
    config = tmp_path / "node.toml"
    config.write_text(f"private_root = {json.dumps(str(private))}\nops_home = {json.dumps(str(home))}\n")
    monkeypatch.setenv("HEALTHCHECK_NODE_CONFIG_PATH", str(config))
    monkeypatch.setenv("HOME", str(home))
    source = tmp_path / "root-interop.json"
    source.write_text(json.dumps({"agent_chat_channel_id": OLD}))
    monkeypatch.setenv("INTEROP_CONFIG", str(source))
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", OLD)
    # When
    result = yoodori_channel.main(["--apply", "--role", "ops", "--yoodori-channel", NEW])
    # Then
    target = private / "channel-override.json"
    assert result == 0
    assert target.is_file()
    _visible(target, home)
    assert target.stat().st_mode & 0o777 == 0o600
    assert approval_directory._configured_agent_chat_channel() == NEW
    assert yoodori_channel.main(["--apply", "--role", "ops", "--yoodori-channel", "off"]) == 0
    assert not target.exists()


def test_default_override_path_is_visible_to_both_repair_units() -> None:
    # Given / When
    config = node_config.default_node_config()
    path = approval_directory.channel_override_path(config)
    # Then
    assert path == config.private_root / "channel-override.json"
    _visible(path, config.ops_home)


def test_sandbox_guard_rejects_an_override_under_ops_home() -> None:
    # Given
    home = node_config.default_node_config().ops_home
    # When / Then
    with pytest.raises(AssertionError, match="hidden"):
        _visible(home / ".hermes/interop/channel-override.json", home)


def test_staged_notice_runtime_has_node_seed_for_override_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: reuse the real copy loop exercised by the notice staging tests.
    from tests.unit import test_deploy_staging_owner_notice as staging

    staging.test_facade_runtime_refreshes_when_local_staging_runs(tmp_path, False)
    runtime = tmp_path / ".hermes/interop_runtime/automation"
    monkeypatch.setenv("HEALTHCHECK_NODE_CONFIG_PATH", str(REPO / "configs/node.example.toml"))
    # When / Then
    assert (runtime / "node.example.toml").read_bytes() == (REPO / "configs/node.example.toml").read_bytes()

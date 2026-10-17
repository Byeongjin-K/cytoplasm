"""W-F3-B — the two ways the subscriber sync tick is deployed.

The tick itself is pinned by test_managed_sync_watch.py. Here we check only the
properties that make each deployment safe to install: the systemd pair must render
through the shared node-asset renderer, and deploy.sh must follow the existing
watcher-deployment convention. Neither may be able to mount anything (D3).
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from automation.node_asset_renderer import render_asset
from automation.node_config import NodeConfig, load_node_config
from tests.unit.cron_fixture import TIMER_SOURCE, declared_cron, timer_call

_REPO = Path(__file__).resolve().parents[2]
_SYSTEMD = _REPO / "automation" / "managed_sync" / "systemd"
_SERVICE = _SYSTEMD / "autophagy-managed-sync.service"
_TIMER = _SYSTEMD / "autophagy-managed-sync.timer"
_DEPLOY = _REPO / "automation" / "managed_sync" / "deploy.sh"


def _third_party_config() -> NodeConfig:
    return replace(
        load_node_config(_REPO / "configs" / "node.example.toml"),
        agent_account="third-agent",
        agent_home=Path("/home/third-agent"),
        release_current=Path("/srv/third/autophagy-agent-current"),
    )


# --- systemd templates -------------------------------------------------------------


@pytest.mark.parametrize("source", [_SERVICE, _TIMER])
def test_units_render_through_the_shared_node_asset_renderer(source: Path) -> None:
    # render_asset raises on any placeholder it cannot resolve, so a typo in a
    # $NODE_ name fails here instead of producing a silently broken unit on a node.
    rendered = render_asset(source, _third_party_config())

    assert "$NODE_" not in rendered


def test_service_runs_the_wrapper_as_the_subscriber_account_with_home_readable() -> None:
    rendered = render_asset(_SERVICE, _third_party_config())

    assert "User=third-agent" in rendered
    assert "Group=third-agent" in rendered
    # Every input the tick reads lives under the agent's $HOME; ProtectHome=yes would
    # make load_config() exit 2 on every tick forever, which looks like a broken install.
    assert "ProtectHome=no" in rendered
    assert (
        "ExecStart=/usr/bin/python3 /srv/third/autophagy-agent-current"
        "/automation/managed_sync/cron/managed_sync_watch.py" in rendered
    )


def test_service_pins_both_roots_the_wrapper_propagates_to_its_child() -> None:
    rendered = render_asset(_SERVICE, _third_party_config())

    assert "Environment=AUTOPHAGY_REPO_ROOT=/srv/third/autophagy-agent-current" in rendered
    assert "Environment=AUTOPHAGY_RUNTIME_ROOT=/srv/third/autophagy-agent-current" in rendered
    assert "Environment=PYTHONPATH=/srv/third/autophagy-agent-current" in rendered


def test_secrets_file_is_optional_so_a_best_effort_notice_cannot_block_delivery() -> None:
    rendered = render_asset(_SERVICE, _third_party_config())

    assert "EnvironmentFile=-/home/third-agent/.env.secrets" in rendered


def test_timer_polls_on_the_thirty_minute_subscriber_contract_and_catches_up() -> None:
    rendered = render_asset(_TIMER, _third_party_config())

    assert "OnUnitActiveSec=30min" in rendered
    assert "Persistent=true" in rendered
    assert "WantedBy=timers.target" in rendered


# --- deploy.sh ---------------------------------------------------------------------


def test_deploy_script_follows_the_existing_watcher_deployment_convention() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")

    # (e) unique filename under ~/.hermes/scripts/
    assert ".hermes/scripts/managed_sync_watch.py" in text
    # provenance guard: never deploy code origin/main does not have
    assert "deploy_provenance_check" in text
    # Idempotent timer convergence, with the helper loaded before the call.
    assert "hermes cron list --all" in text
    call = timer_call(declared_cron("automation/managed_sync"))
    assert call in text and text.index(TIMER_SOURCE) < text.index(call)
    assert declared_cron("automation/managed_sync").attr("mode") == "no-agent"


# --- D3: neither deployment may activate a release ---------------------------------


@pytest.mark.parametrize("source", [_SERVICE, _TIMER, _DEPLOY])
def test_no_deployment_asset_can_mount_a_release(source: Path) -> None:
    # Delivery to quarantine is automated by this wave; MOUNTING is not. An asset that
    # could reach the mount path would silently convert this into auto-activation.
    text = source.read_text(encoding="utf-8")

    assert "deploy-skill.sh" not in text
    assert "--activate-managed" not in text

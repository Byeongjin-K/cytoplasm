from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

from automation.install.assets import build_inputs, render_node_toml
from automation.install.plan import EnsureFile, SystemState, build_plan
from automation.node_config import load_node_config


_REPO = Path(__file__).resolve().parents[2]


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    material = len(algorithm).to_bytes(4, "big") + algorithm
    material += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(material).decode()} installer-test"


def test_build_inputs_renders_node_bound_units_helpers_hooks_and_runtime_config(
    tmp_path: Path,
) -> None:
    config = replace(
        load_node_config(_REPO / "configs" / "node.example.toml"),
        agent_account="third-agent",
        peer_account="third-peer",
        ops_account="third-ops",
        service_group="third-services",
        primary_node_name="third-node",
        agent_home=Path("/home/third-agent"),
        peer_home=Path("/home/third-peer"),
        ops_home=Path("/home/third-ops"),
        service_root=Path("/srv/third"),
        deploy_checkout=Path("/srv/third/checkout"),
        repair_work=Path("/srv/third/repair"),
        release_current=Path("/srv/third/autophagy-agent-current"),
        release_store=Path("/srv/third/autophagy-agent-releases"),
        libexec_dir=Path("/opt/third/libexec"),
    )

    inputs = build_inputs(_REPO, config, _public_key())
    files = {spec.path: spec for spec in inputs.files}

    service = files[Path("/etc/systemd/system/autophagy-deploy-reconcile.service")]
    assert "User=third-ops" in service.content
    assert "WorkingDirectory=/srv/third/autophagy-agent-current" in service.content
    helper = files[Path("/opt/third/libexec/autophagy-converge-origin-main")]
    assert helper.mode == 0o755
    assert "MIRROR=/srv/third/checkout" in helper.content
    assert "STORE_PARENT=/srv/third" in helper.content
    assert "LIBDIR=/opt/third/libexec/autophagy-converge.d" in helper.content
    assert files[Path("/srv/third/checkout/.git/hooks/pre-commit")].mode == 0o755
    assert "REFUSED" in files[Path("/srv/third/checkout/.git/hooks/pre-commit")].content
    repair_hook = files[Path("/srv/third/repair/.git/hooks/pre-commit")].content
    assert "gitleaks git --pre-commit --staged --redact --verbose" in repair_hook

    node_toml = tmp_path / "node.toml"
    _ = node_toml.write_text(
        files[Path("/home/third-ops/.hermes/node.toml")].content,
        encoding="utf-8",
    )
    assert load_node_config(node_toml) == replace(config, peer_attest_mode="signed")


def test_build_inputs_installs_node_config_for_reconciler_and_runtime_accounts() -> None:
    config = load_node_config(_REPO / "configs" / "node.example.toml")

    inputs = build_inputs(_REPO, config, _public_key())
    files = {spec.path: spec for spec in inputs.files}
    rendered = render_node_toml(replace(config, peer_attest_mode="signed"))

    reconciler = files[Path("/etc/autophagy/node.toml")]
    assert reconciler.content == rendered
    assert reconciler.mode == 0o644
    assert reconciler.owner == "root"
    assert reconciler.group == "root"

    for account, home in (
        (config.agent_account, config.agent_home),
        (config.peer_account, config.peer_home),
        (config.ops_account, config.ops_home),
    ):
        runtime = files[home / ".hermes" / "node.toml"]
        assert runtime.content == reconciler.content
        assert runtime.mode == 0o600
        assert runtime.owner == account
        assert runtime.group == account


def test_build_inputs_installs_command_sync_dropins_for_both_gateways() -> None:
    config = replace(
        load_node_config(_REPO / "configs" / "node.example.toml"),
        agent_account="third-agent",
        peer_account="third-peer",
        agent_home=Path("/home/third-agent"),
        peer_home=Path("/home/third-peer"),
        agent_gateway_unit="third-agent-gateway.service",
        peer_gateway_unit="third-peer-gateway.service",
    )

    inputs = build_inputs(_REPO, config, _public_key())
    files = {spec.path: spec for spec in inputs.files}
    content = "[Service]\nEnvironment=DISCORD_COMMAND_SYNC_POLICY=bulk\n"
    expected = {
        Path(
            "/home/third-agent/.config/systemd/user/"
            "third-agent-gateway.service.d/30-command-sync.conf"
        ): "third-agent",
        Path(
            "/home/third-peer/.config/systemd/user/"
            "third-peer-gateway.service.d/30-command-sync.conf"
        ): "third-peer",
    }

    for path, account in expected.items():
        dropin = files[path]
        assert dropin.content == content
        assert dropin.mode == 0o600
        assert dropin.owner == account
        assert dropin.group == account


def test_build_inputs_renders_healthcheck_peer_read_sudoers_dropin_exactly() -> None:
    # Given
    config = load_node_config(_REPO / "configs" / "node.example.toml")

    # When
    inputs = build_inputs(_REPO, config, _public_key())
    files = {spec.path: spec for spec in inputs.files}

    # Then
    peer_read = files[Path("/etc/sudoers.d/autophagy-healthcheck-peer-read")]
    assert peer_read.content == (
        "ops ALL=(peer) NOPASSWD: /usr/bin/cat -- /home/peer/.hermes/config.yaml\n"
        "ops ALL=(peer) NOPASSWD: /usr/bin/cat -- /home/peer/.hermes/channel_directory.json\n"
    )
    assert (peer_read.mode, peer_read.owner, peer_read.group) == (0o440, "root", "root")


def test_build_inputs_renders_healthcheck_peer_read_sudoers_from_custom_node_config() -> None:
    # Given
    config = replace(
        load_node_config(_REPO / "configs" / "node.example.toml"),
        ops_account="alternate-ops",
        peer_account="alternate-peer",
        peer_home=Path("/srv/alternate-peer"),
    )

    # When
    inputs = build_inputs(_REPO, config, _public_key())
    files = {spec.path: spec for spec in inputs.files}

    # Then
    peer_read = files[Path("/etc/sudoers.d/autophagy-healthcheck-peer-read")]
    assert peer_read.content == (
        "alternate-ops ALL=(alternate-peer) NOPASSWD: /usr/bin/cat -- "
        "/srv/alternate-peer/.hermes/config.yaml\n"
        "alternate-ops ALL=(alternate-peer) NOPASSWD: /usr/bin/cat -- "
        "/srv/alternate-peer/.hermes/channel_directory.json\n"
    )


def test_healthcheck_peer_read_sudoers_dropin_is_idempotent_after_first_plan() -> None:
    # Given
    config = load_node_config(_REPO / "configs" / "node.example.toml")
    inputs = build_inputs(_REPO, config, _public_key())
    initial = build_plan(inputs, SystemState.empty())

    # When
    repeated = build_plan(inputs, SystemState.from_actions(initial.actions))

    # Then
    target = Path("/etc/sudoers.d/autophagy-healthcheck-peer-read")
    assert any(
        action.spec.path == target
        for action in initial.actions
        if isinstance(action, EnsureFile)
    )
    assert all(
        action.spec.path != target
        for action in repeated.actions
        if isinstance(action, EnsureFile)
    )


def test_update_trust_file_comes_from_existing_bootstrap_contract() -> None:
    config = load_node_config(_REPO / "configs" / "node.example.toml")

    inputs = build_inputs(_REPO, config, _public_key())
    trust = next(spec for spec in inputs.files if spec.path == Path("/etc/autophagy/update-allowed-signers"))

    assert trust.mode == 0o644
    assert trust.owner == "root" and trust.group == "root"
    assert 'namespaces="git"' in trust.content

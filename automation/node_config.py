"""Typed node identity, account, path, and gateway configuration."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias, cast


_ACCOUNT: Final = re.compile(r"^[a-z_][a-z0-9_-]*$")
_NODE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UNIT: Final = re.compile(r"^[A-Za-z0-9_.@-]+\.service$")
_ConfigValue: TypeAlias = str | bool
PeerAttestMode: TypeAlias = Literal["discord", "signed"]
_FIELD_NAMES: Final = frozenset({
    "origin_url", "require_signed_updates", "peer_attest_mode", "primary_node_name", "rag_node_name", "deploy_ssh_host",
    "operator_account", "agent_account", "peer_account", "ops_account",
    "service_group", "service_root", "deploy_checkout", "release_current",
    "release_store", "private_root", "skill_store", "repair_work",
    "repair_report_queue", "repair_report_ack", "repair_capability", "libexec_dir",
    "agent_home", "peer_home", "ops_home", "agent_gateway_unit", "peer_gateway_unit",
})


# The override lives outside any HOME on purpose. Every consumer that reads it runs
# somewhere HOME is unreliable: the reconciler unit sets ProtectHome=tmpfs, and the
# approval-resume helper runs the pipeline under `env -i HOME=/root`. Keying the
# config off Path.home() meant each of those silently fell back to the seed and used
# placeholder hostnames — three separate silent failures on 2026-08-16 alone.
# /etc/autophagy is where this system already keeps root-owned trust material.
SYSTEM_NODE_CONFIG_PATH: Final = Path("/etc/autophagy/node.toml")


def _override_path(path: Path | None) -> Path:
    if path is not None:
        return path
    if SYSTEM_NODE_CONFIG_PATH.exists():
        return SYSTEM_NODE_CONFIG_PATH
    return Path.home() / ".hermes" / "node.toml"


def _seed_path() -> Path:
    installed = Path(__file__).with_name("node.example.toml")
    return installed if installed.is_file() else Path(__file__).resolve().parents[1] / "configs" / "node.example.toml"


class NodeConfigError(RuntimeError):
    """The node configuration cannot be parsed into a safe complete value."""


@dataclass(frozen=True, slots=True)
class NodeConfig:
    """One installation's resolved node topology and filesystem layout."""

    origin_url: str
    require_signed_updates: bool
    peer_attest_mode: PeerAttestMode
    primary_node_name: str
    rag_node_name: str
    deploy_ssh_host: str
    operator_account: str
    agent_account: str
    peer_account: str
    ops_account: str
    service_group: str
    service_root: Path
    deploy_checkout: Path
    release_current: Path
    release_store: Path
    private_root: Path
    skill_store: Path
    repair_work: Path
    repair_report_queue: Path
    repair_report_ack: Path
    repair_capability: Path
    libexec_dir: Path
    agent_home: Path
    peer_home: Path
    ops_home: Path
    agent_gateway_unit: str
    peer_gateway_unit: str


def default_node_config() -> NodeConfig:
    """Load the tracked production-compatible seed used when no override exists."""
    return _load_complete(_seed_path())


#: A caller that NAMES a path has already decided which file is authoritative, so a
#: missing one is an error. Answering "read this file" with "here is the seed instead" is
#: how the reconciler's two verification paths came to enforce different policies: the
#: root helper named `~ops/.hermes/node.toml`, that file did not exist, and the seed
#: answered in its place (2026-08-21). Only the unnamed lookup may fall back.
def load_node_config(path: Path | None = None) -> NodeConfig:
    """Load an optional runtime override, rejecting malformed or unknown input."""
    if path is not None and not path.exists():
        raise NodeConfigError(f"node configuration is absent: {path}")
    config_path = _override_path(path)
    if not config_path.exists():
        return default_node_config()
    raw = _read_values(config_path, "node configuration")
    defaults = default_node_config()
    unknown = set(raw) - _FIELD_NAMES
    if unknown:
        raise NodeConfigError(f"unknown node configuration fields: {', '.join(sorted(unknown))}")
    return _validate(_from_values(raw, defaults, require_signed_updates_default=True))


def _load_complete(path: Path) -> NodeConfig:
    raw = _read_values(path, "node seed")
    if frozenset(raw) != _FIELD_NAMES:
        raise NodeConfigError("node seed must define every known field exactly once")
    return _validate(_from_values(raw))


def _read_values(path: Path, label: str) -> Mapping[str, _ConfigValue]:
    try:
        with path.open("rb") as stream:
            parsed_raw = cast(dict[str, object], tomllib.load(stream))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise NodeConfigError(f"{label} is unreadable or malformed: {path}") from error
    parsed: dict[str, _ConfigValue] = {}
    for name, value in parsed_raw.items():
        if not isinstance(value, (str, bool)):
            raise NodeConfigError(f"{label} field must be a string or boolean: {name}")
        parsed[name] = value
    return parsed


def _from_values(
    values: Mapping[str, _ConfigValue],
    fallback: NodeConfig | None = None,
    *,
    require_signed_updates_default: bool = True,
) -> NodeConfig:
    def text(name: str, default: str = "") -> str:
        value = values.get(name, default)
        if not isinstance(value, str):
            raise NodeConfigError(f"node configuration field must be a string: {name}")
        return value

    def flag(name: str, default: bool) -> bool:
        value = values.get(name, default)
        if not isinstance(value, bool):
            raise NodeConfigError(f"node configuration field must be a boolean: {name}")
        return value

    def peer_mode(default: PeerAttestMode) -> PeerAttestMode:
        match text("peer_attest_mode", default):
            case "discord":
                return "discord"
            case "signed":
                return "signed"
            case invalid:
                raise NodeConfigError(f"peer_attest_mode must be discord or signed: {invalid}")

    def path(name: str, default: Path | None = None) -> Path:
        return Path(text(name, "" if default is None else str(default)))

    base = fallback
    return NodeConfig(
        origin_url=text("origin_url", "" if base is None else base.origin_url),
        require_signed_updates=flag(
            "require_signed_updates",
            require_signed_updates_default,
        ),
        peer_attest_mode=peer_mode("discord" if base is None else base.peer_attest_mode),
        primary_node_name=text("primary_node_name", "" if base is None else base.primary_node_name),
        rag_node_name=text("rag_node_name", "" if base is None else base.rag_node_name),
        deploy_ssh_host=text("deploy_ssh_host", "" if base is None else base.deploy_ssh_host),
        operator_account=text("operator_account", "" if base is None else base.operator_account),
        agent_account=text("agent_account", "" if base is None else base.agent_account),
        peer_account=text("peer_account", "" if base is None else base.peer_account),
        ops_account=text("ops_account", "" if base is None else base.ops_account),
        service_group=text("service_group", "" if base is None else base.service_group),
        service_root=path("service_root", None if base is None else base.service_root),
        deploy_checkout=path("deploy_checkout", None if base is None else base.deploy_checkout),
        release_current=path("release_current", None if base is None else base.release_current),
        release_store=path("release_store", None if base is None else base.release_store),
        private_root=path("private_root", None if base is None else base.private_root),
        skill_store=path("skill_store", None if base is None else base.skill_store),
        repair_work=path("repair_work", None if base is None else base.repair_work),
        repair_report_queue=path("repair_report_queue", None if base is None else base.repair_report_queue),
        repair_report_ack=path("repair_report_ack", None if base is None else base.repair_report_ack),
        repair_capability=path("repair_capability", None if base is None else base.repair_capability),
        libexec_dir=path("libexec_dir", None if base is None else base.libexec_dir),
        agent_home=path("agent_home", None if base is None else base.agent_home),
        peer_home=path("peer_home", None if base is None else base.peer_home),
        ops_home=path("ops_home", None if base is None else base.ops_home),
        agent_gateway_unit=text("agent_gateway_unit", "" if base is None else base.agent_gateway_unit),
        peer_gateway_unit=text("peer_gateway_unit", "" if base is None else base.peer_gateway_unit),
    )


def _reject_unprintable(config: NodeConfig) -> None:
    """Reject control characters in every field that reaches a rendered asset.

    Every value below is substituted verbatim into root-owned ``/etc/sudoers.d``
    and ``/etc/systemd/system`` assets by ``node_asset_renderer``, where a single
    embedded newline becomes an additional, grammatically valid directive that
    ``visudo -cf`` happily accepts. The account, node, and unit fields already
    carried charset regexes (``_ACCOUNT``/``_NODE``/``_UNIT``); the path, URL,
    and host fields did not. Iterating ``node_config_values`` — the exact set the
    renderer substitutes — keeps future fields covered by construction.

    ``str.isprintable`` rejects control, format, surrogate, and separator
    characters while keeping ordinary spaces and non-ASCII letters, so genuine
    paths, URLs, and hostnames are unaffected.
    """
    for name, value in node_config_values(config).items():
        if not value.isprintable():
            raise NodeConfigError(f"{name} must not contain control characters")


def _validate(config: NodeConfig) -> NodeConfig:
    _reject_unprintable(config)
    if not config.origin_url:
        raise NodeConfigError("origin_url must not be empty")
    if config.deploy_ssh_host.startswith("-"):
        raise NodeConfigError("deploy_ssh_host must not begin with '-'")
    if config.peer_attest_mode not in ("discord", "signed"):
        raise NodeConfigError("peer_attest_mode must be discord or signed")
    for name, value in (
        ("primary_node_name", config.primary_node_name),
        ("rag_node_name", config.rag_node_name),
    ):
        if _NODE.fullmatch(value) is None:
            raise NodeConfigError(f"{name} is not a valid node name")
    for name, value in (
        ("operator_account", config.operator_account),
        ("agent_account", config.agent_account),
        ("peer_account", config.peer_account),
        ("ops_account", config.ops_account),
        ("service_group", config.service_group),
    ):
        if _ACCOUNT.fullmatch(value) is None:
            raise NodeConfigError(f"{name} is not a valid Unix name")
    for name, value in (
        ("service_root", config.service_root),
        ("deploy_checkout", config.deploy_checkout),
        ("release_current", config.release_current),
        ("release_store", config.release_store),
        ("private_root", config.private_root),
        ("skill_store", config.skill_store),
        ("repair_work", config.repair_work),
        ("repair_report_queue", config.repair_report_queue),
        ("repair_report_ack", config.repair_report_ack),
        ("repair_capability", config.repair_capability),
        ("libexec_dir", config.libexec_dir),
        ("agent_home", config.agent_home),
        ("peer_home", config.peer_home),
        ("ops_home", config.ops_home),
    ):
        if not value.is_absolute():
            raise NodeConfigError(f"{name} must be an absolute path")
    for name, value in (
        ("agent_gateway_unit", config.agent_gateway_unit),
        ("peer_gateway_unit", config.peer_gateway_unit),
    ):
        if _UNIT.fullmatch(value) is None:
            raise NodeConfigError(f"{name} must name a .service unit")
    return config


def node_config_values(config: NodeConfig) -> Mapping[str, str]:
    return {
        "origin_url": config.origin_url,
        "require_signed_updates": "1" if config.require_signed_updates else "0",
        "peer_attest_mode": config.peer_attest_mode,
        "primary_node_name": config.primary_node_name,
        "rag_node_name": config.rag_node_name,
        "deploy_ssh_host": config.deploy_ssh_host,
        "operator_account": config.operator_account,
        "agent_account": config.agent_account,
        "peer_account": config.peer_account,
        "ops_account": config.ops_account,
        "service_group": config.service_group,
        "service_root": str(config.service_root),
        "deploy_checkout": str(config.deploy_checkout),
        "release_current": str(config.release_current),
        "release_store": str(config.release_store),
        "private_root": str(config.private_root),
        "skill_store": str(config.skill_store),
        "repair_work": str(config.repair_work),
        "repair_report_queue": str(config.repair_report_queue),
        "repair_report_ack": str(config.repair_report_ack),
        "repair_capability": str(config.repair_capability),
        "libexec_dir": str(config.libexec_dir),
        "agent_home": str(config.agent_home),
        "peer_home": str(config.peer_home),
        "ops_home": str(config.ops_home),
        "agent_gateway_unit": config.agent_gateway_unit,
        "peer_gateway_unit": config.peer_gateway_unit,
    }

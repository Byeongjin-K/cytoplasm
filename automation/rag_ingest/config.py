"""Ingest configuration loading (agent-local JSON + secrets file).

The deployed config lives at ``~/.hermes/rag-ingest/config.json`` (mode 600,
agent-only). The repo ships ``config.example.json`` with placeholders only —
no guild ids, hostnames beyond LAN aliases, or secrets in git.

Hermes conversations and agents-log indexing default off, including for existing
configs. Explicit ``index_hermes_conversations`` and ``discord.index_agents_log``
opt back in; other sources and already indexed records are untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "rag-ingest" / "config.json"
DEFAULT_OBSIDIAN_EXCLUDE_NAMES = (
    ".obsidian",
    ".omo",
    ".sisyphus",
    ".omo-backup*",
    "999_limbo",
    "Excalidraw",
    ".claude",
    ".cursor",
    ".playwright-mcp",
)


class ConfigError(Exception):
    """Missing/invalid configuration — fatal, needs operator action."""


@dataclass(frozen=True)
class DiscordSourceConfig:
    enabled: bool
    guild_id: str
    team_channel: str
    agents_log_channel: str
    token_env: str
    bootstrap_limit: int = 50


@dataclass(frozen=True, slots=True)
class ObsidianSourceConfig:
    enabled: bool
    repo_url: str
    mirror_dir: Path
    ssh_key_path: Path
    branch: str = "main"
    exclude_names: tuple[str, ...] = DEFAULT_OBSIDIAN_EXCLUDE_NAMES


@dataclass(frozen=True)
class IngestConfig:
    mcp_base_url: str
    api_key: str
    state_dir: Path
    wiki_dir: Path
    notes_dir: Path
    meetings_dir: Path
    hermes_db: Path | None
    perspective: dict[str, str]
    discord: DiscordSourceConfig | None
    obsidian: ObsidianSourceConfig | None = None
    discord_token: str = field(repr=False, default="")
    max_chunk_chars: int = 1500

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def queue_path(self) -> Path:
        return self.state_dir / "queue.jsonl"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"


def read_secret(secrets_file: Path, key_name: str) -> str:
    """Read one KEY=VALUE entry from an env-style secrets file."""
    if not secrets_file.exists():
        raise ConfigError(f"secrets file missing: {secrets_file}")
    for line in secrets_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == key_name:
            secret = value.strip()
            if secret:
                return secret
    raise ConfigError(f"secret {key_name} not found in {secrets_file}")


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"config key missing or not a string: {key}")
    return value


def _parse_obsidian(raw: dict[str, Any]) -> ObsidianSourceConfig | None:
    obsidian_raw = raw.get("obsidian")
    if obsidian_raw is None:
        return None
    if not isinstance(obsidian_raw, dict):
        raise ConfigError("config key must be a JSON object: obsidian")

    enabled = obsidian_raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("config key missing or not a boolean: obsidian.enabled")
    if not enabled:
        return None

    branch_raw = obsidian_raw.get("branch", "main")
    if not isinstance(branch_raw, str) or not branch_raw:
        raise ConfigError("config key missing or not a string: obsidian.branch")

    if "exclude_names" not in obsidian_raw:
        exclude_names = DEFAULT_OBSIDIAN_EXCLUDE_NAMES
    else:
        exclude_names_raw = obsidian_raw["exclude_names"]
        if not isinstance(exclude_names_raw, list) or not all(
            isinstance(name, str) and name for name in exclude_names_raw
        ):
            raise ConfigError("config key missing or not a list of strings: obsidian.exclude_names")
        exclude_names = tuple(exclude_names_raw)

    return ObsidianSourceConfig(
        enabled=True,
        repo_url=_require_str(obsidian_raw, "repo_url"),
        mirror_dir=Path(_require_str(obsidian_raw, "mirror_dir")).expanduser(),
        ssh_key_path=Path(_require_str(obsidian_raw, "ssh_key_path")).expanduser(),
        branch=branch_raw,
        exclude_names=exclude_names,
    )


def load_config(path: Path) -> IngestConfig:
    if not path.exists():
        raise ConfigError(f"config file missing: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config JSON invalid: {path}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a JSON object")

    secrets_file = Path(_require_str(raw, "secrets_file")).expanduser()
    api_key = read_secret(secrets_file, _require_str(raw, "api_key_env"))

    perspective_raw = raw.get("perspective")
    if not isinstance(perspective_raw, dict):
        raise ConfigError("config key missing: perspective")
    perspective = {str(key): str(value) for key, value in perspective_raw.items()}

    discord_raw = raw.get("discord")
    discord: DiscordSourceConfig | None = None
    discord_token = ""
    if isinstance(discord_raw, dict) and discord_raw.get("enabled"):
        discord = DiscordSourceConfig(
            enabled=True,
            guild_id=str(discord_raw.get("guild_id", "")),
            team_channel=str(discord_raw.get("team_channel", "team")),
            agents_log_channel=(str(discord_raw.get("agents_log_channel", "agents-log"))
                                if discord_raw.get("index_agents_log") is True else ""),
            token_env=str(discord_raw.get("token_env", "DISCORD_BOT_TOKEN")),
            bootstrap_limit=int(discord_raw.get("bootstrap_limit", 50)),
        )
        if not discord.guild_id:
            raise ConfigError("discord.enabled requires discord.guild_id")
        discord_token = read_secret(secrets_file, discord.token_env)

    obsidian = _parse_obsidian(raw)

    hermes_db_raw = raw.get("hermes_db") if raw.get("index_hermes_conversations") is True else None
    hermes_db = Path(str(hermes_db_raw)).expanduser() if hermes_db_raw else None

    return IngestConfig(
        mcp_base_url=_require_str(raw, "mcp_base_url").rstrip("/"),
        api_key=api_key,
        state_dir=Path(_require_str(raw, "state_dir")).expanduser(),
        wiki_dir=Path(_require_str(raw, "wiki_dir")).expanduser(),
        notes_dir=Path(_require_str(raw, "notes_dir")).expanduser(),
        meetings_dir=Path(_require_str(raw, "meetings_dir")).expanduser(),
        hermes_db=hermes_db,
        perspective=perspective,
        discord=discord,
        obsidian=obsidian,
        discord_token=discord_token,
        max_chunk_chars=int(raw.get("max_chunk_chars", 1500)),
    )

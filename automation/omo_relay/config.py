"""Relay coordinates live outside the node config so the root helper copies never change."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

KEYS = frozenset({"omo_relay_target", "omo_relay_key"})
RELEASE_HOST_FILE = Path(".hermes/omo-relay.toml")


class RelayConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RelayConfig:
    omo_relay_target: str
    omo_relay_key: Path


def parse(text: str) -> RelayConfig:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise RelayConfigError("relay config is not TOML") from error
    if set(raw) != KEYS or not all(isinstance(raw[key], str) for key in KEYS):
        raise RelayConfigError("relay config needs exactly omo_relay_target and omo_relay_key")
    target, key = raw["omo_relay_target"], Path(raw["omo_relay_key"])
    if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", target) is None:
        raise RelayConfigError("omo_relay_target must be a safe SSH target")
    if not key.is_absolute():
        raise RelayConfigError("omo_relay_key must be absolute")
    return RelayConfig(target, key)


def load(path: Path) -> RelayConfig:
    return parse(path.read_text())


def release_host(home: Path) -> RelayConfig:
    return load(home / RELEASE_HOST_FILE)

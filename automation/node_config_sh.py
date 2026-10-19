"""Shell bridge for the typed node configuration."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation import node_config
from automation.node_config import load_node_config, node_config_values

#: Where the bridge looks for the system override instead of /etc/autophagy/node.toml.
#: Unit tests point it at an absent path so a node and a workstation resolve alike; the
#: caller could already name any file through HEALTHCHECK_NODE_CONFIG_PATH, and sudo's
#: env_reset keeps it from crossing into a privileged run.
SYSTEM_PATH_ENV = "AUTOPHAGY_SYSTEM_NODE_CONFIG"


def apply_system_override() -> None:
    """Honour SYSTEM_PATH_ENV for this process's later node config loads."""
    system = os.environ.get(SYSTEM_PATH_ENV, "").strip()
    if system:
        node_config.SYSTEM_NODE_CONFIG_PATH = Path(system)  # pyright: ignore[reportConstantRedefinition]


def print_env() -> None:
    """Print shell-sourceable assignments for every resolved field."""
    apply_system_override()
    configured = os.environ.get("HEALTHCHECK_NODE_CONFIG_PATH")
    config = load_node_config(Path(configured) if configured else None)
    for name, value in node_config_values(config).items():
        print(f"NODE_{name.upper()}={shlex.quote(value)}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="node-config")
    _ = parser.add_argument("--print-env", action="store_true", required=True)
    _ = parser.parse_args()
    print_env()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

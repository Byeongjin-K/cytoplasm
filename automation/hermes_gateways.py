"""The release host owns the stop switch; node probes only read the deployed marker."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

STOP_MARKER: Final = ".hermes/gateway-stopped-by-release"


def release_switch(home: Path) -> str:
    """Only the exact word `stopped` opts in; missing means running."""
    try:
        value = (home / ".hermes/release-flags/hermes-gateways-stopped").read_bytes()
    except FileNotFoundError:
        return "running"
    if value != b"stopped":
        raise ValueError("GATEWAY-STOP-REFUSED: expected the literal word stopped only")
    return "stopped"


def stopped_by_release(home: Path) -> bool:
    """An unreadable or malformed node marker cannot hide a gateway fault."""
    try:
        return (home / STOP_MARKER).read_bytes() == b"stopped"
    except OSError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.hermes_gateways")
    parser.add_argument("--release-switch", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        print(release_switch(Path.home()))
    except (OSError, ValueError) as error:
        print(f"GATEWAY-STOP-REFUSED reason={type(error).__name__}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

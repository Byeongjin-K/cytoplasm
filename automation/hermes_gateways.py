"""The release host owns the stop switch; node probes only read the deployed marker."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

STOP_MARKER: Final = ".hermes/gateway-stopped-by-release"


def stopped_in_receipt(path: Path, accounts: Sequence[str], marker: Path | None = None) -> bool:
    """Retain the last fully applied mode; ops can read this outside protected homes."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # No receipt yet: an approved stop leaves an ops marker (PR #104 gap 8). Without one this
        # is a legacy installation before its first deploy-all receipt.
        try:
            return marker is not None and marker.read_bytes() == b"stopped"
        except FileNotFoundError:
            return False
    if not isinstance(raw, dict):
        raise ValueError("GATEWAY-RECEIPT-INVALID: expected an object")
    sha = raw.get("release_sha")
    surfaces = raw.get("surfaces")
    if (type(raw.get("version")) is not int or raw["version"] not in (1, 2)
            or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40,64}", sha) is None
            or not isinstance(surfaces, dict)):
        raise ValueError("GATEWAY-RECEIPT-INVALID: incomplete deployment receipt")
    if "hermes_gateways" not in raw:
        return False  # Receipts written before the release stop switch existed.
    mode = raw["hermes_gateways"]
    if mode == "retired":
        if (raw["version"] != 2 or raw.get("peer_retired") != "retired"
                or surfaces.get("gateway_services") != {}):
            raise ValueError("GATEWAY-RECEIPT-INVALID: incomplete retirement receipt")
        return True
    if mode not in ("running", "stopped"):
        raise ValueError("GATEWAY-RECEIPT-INVALID: unknown gateway mode")
    if (raw["version"] != 2
            or surfaces.get("gateway_services") != {account: mode for account in accounts}):
        raise ValueError("GATEWAY-RECEIPT-INVALID: incomplete gateway observation")
    return mode == "stopped"


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

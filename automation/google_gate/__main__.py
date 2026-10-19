"""Explicit local preparation CLI; no implicit activation or node access."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from automation.google_gate import config, shim
from automation.google_gate.token_client import GateError


def main() -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.google_gate")
    sub = parser.add_subparsers(dest="command", required=True)
    config.add_arguments(sub.add_parser("render-config"))
    sh = sub.add_parser("shim")
    sh.add_argument("action", choices=("install", "remove", "status"))
    sh.add_argument("--home", type=Path, default=Path.home())
    sh.add_argument("--port", type=int, default=3129)
    try:
        args = parser.parse_args()
        if args.command == "render-config":
            print(config.render(args), end="")
        else:
            print(shim.change(args.action, shim.Paths(args.home.absolute()), args.port))
        return 0
    except GateError as error:
        print(f"GOOGLE-GATE-REFUSED {error.code}", file=sys.stderr)
    except OSError:
        print("GOOGLE-GATE-REFUSED filesystem", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

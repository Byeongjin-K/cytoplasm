"""Agent-only release profile: existing memory selection plus YooDori cutover.

Keep one owner for (agent, .hermes/config.yaml). The peer still uses config.py
unchanged; both selections here are prepared before a single backup and rename.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from automation import hermes_agent_chat_ignore as ignore
from automation import hermes_memory_config as limits
from automation.memory_provider import config as memory

ALGORITHM: Final = "hermes-agent-profile-v1"
MARKER: Final = "HERMES-AGENT-PROFILE"


def wanted_identity() -> str:
    return f"{memory.wanted_identity()},{ignore.wanted_identity()}"


def identity(text: str, channel: str) -> str:
    return f"{memory.read_profile(text).identity()},{ignore.identity(text, channel)}"


def apply_profile(text: str, channel: str) -> str:
    """Compose the existing pure memory editor with the list-only editor."""
    return ignore.apply_ignore(memory.apply_profile(text), channel)


def main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.memory_provider.agent_config")
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ("show", "check", "apply"):
        mode.add_argument(f"--{name}", action="store_true")
    args = parser.parse_args(argv)
    root = (home or Path.home()) / ".hermes"
    path = root / "config.yaml"
    try:
        channel = ignore.agent_chat_channel(root)
        text = path.read_bytes().decode("utf-8")
        current = identity(text, channel)
        profile = memory.read_profile(text)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"{MARKER}-ERROR reason={error.__class__.__name__}", file=sys.stderr)
        return 3
    if args.show:
        print(f"{MARKER} have={current}")
        return 0
    in_sync = current == wanted_identity()
    if args.check:
        print(f"{MARKER} {'ok' if in_sync else 'drift'} have={current} want={wanted_identity()}")
        return 0 if in_sync else 1
    if profile.provider not in {None, "builtin", memory.PROVIDER}:
        print(f"{MARKER}-REFUSED reason=external-provider-selected", file=sys.stderr)
        return 7
    if in_sync:
        print(f"{MARKER} unchanged have={current}")
        return 0
    try:
        if limits.lowering_blocked(profile.limits, root / "memories"):
            print(f"{MARKER}-REFUSED reason=memory-above-lowered-cap", file=sys.stderr)
            return 7
        after = apply_profile(text, channel)
        ignore.write_config(path, after)
        readback = path.read_bytes().decode("utf-8")
        if readback != after or identity(readback, channel) != wanted_identity():
            print(f"{MARKER}-ERROR reason=readback", file=sys.stderr)
            return 5
    except (OSError, UnicodeError, ValueError) as error:
        print(f"{MARKER}-ERROR reason={error.__class__.__name__}", file=sys.stderr)
        return 3
    print(f"{MARKER} applied have={wanted_identity()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

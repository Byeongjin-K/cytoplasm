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


def wanted_identity(
    *, dori_agent_chat: bool | None = None, home: Path | None = None,
    agent_chat_channel: str = "off",
) -> str:
    """Host observation reads the switch; node callers always supply the mode."""
    if dori_agent_chat is None:
        dori_agent_chat = ignore.release_switch(home or Path.home()) is not None
    moving = agent_chat_channel != "off"
    base = f"{memory.wanted_identity()},agent_chat_ignored={'yes' if dori_agent_chat or moving else 'no'}"
    return base + (",agent_chat_target_ignored=yes,agent_chat_free_response=no" if moving else "")


def identity(text: str, channel: str | None, target: str = "off") -> str:
    ignored = ignore.identity(text, channel) if channel else "agent_chat_ignored=no"
    base = f"{memory.read_profile(text).identity()},{ignored}"
    if target != "off":
        free = ignore.read_ignored(text, key_name="free_response_channels").channels
        base += (f",agent_chat_target_ignored={'yes' if target in ignore.read_ignored(text).channels else 'no'}"
                 f",agent_chat_free_response={'yes' if channel in free else 'no'}")
    return base


def apply_profile(text: str, channel: str | None, *, dori_agent_chat: bool = False) -> str:
    """Compose the existing pure memory editor with the list-only editor."""
    after = memory.apply_profile(text)
    return ignore.apply_ignore(after, channel, remove=not dori_agent_chat) if channel else after


def main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.memory_provider.agent_config")
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ("show", "check", "apply"):
        mode.add_argument(f"--{name}", action="store_true")
    parser.add_argument("--dori-agent-chat", choices=("on", "off"))
    parser.add_argument("--yoodori-channel", default="off")
    args = parser.parse_args(argv)
    if not args.show and args.dori_agent_chat is None:
        parser.error("--check/--apply require --dori-agent-chat on|off")
    root = (home or Path.home()) / ".hermes"
    path = root / "config.yaml"
    target = args.yoodori_channel
    previous = None
    try:
        channel = ignore.agent_chat_channel(root)
        if target != "off" or (root / "interop/config.json").is_file():
            from automation.yoodori_channel import original_chat

            channel, previous = original_chat(root / "interop/config.json")
    except (OSError, UnicodeError, ValueError):
        channel = None
    original = previous or channel
    if (args.dori_agent_chat == "on" or target != "off") and not args.show and original is None:
        print(f"{MARKER}-ERROR reason=missing-agent-chat-channel", file=sys.stderr)
        return 3
    wanted = wanted_identity(dori_agent_chat=args.dori_agent_chat == "on", agent_chat_channel=target)
    try:
        text = path.read_bytes().decode("utf-8")
        current = identity(text, original, target)
        profile = memory.read_profile(text)
        after = apply_profile(text, original, dori_agent_chat=args.dori_agent_chat == "on" or target != "off")
        if target != "off":
            after = ignore.apply_ignore(after, target)
            if previous and channel not in {original, target}:
                after = ignore.apply_ignore(after, channel, remove=True)
            if original:
                after = ignore.apply_ignore(after, original, remove=True, key_name="free_response_channels")
        elif previous and channel != previous:
            after = ignore.apply_ignore(after, channel, remove=True)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"{MARKER}-ERROR reason={error.__class__.__name__}", file=sys.stderr)
        return 3
    if args.show:
        print(f"{MARKER} have={current}")
        return 0
    in_sync = current == wanted and after == text
    if args.check:
        print(f"{MARKER} {'ok' if in_sync else 'drift'} have={current} want={wanted}")
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
        ignore.write_config(path, after)
        readback = path.read_bytes().decode("utf-8")
        if readback != after or identity(readback, original, target) != wanted:
            print(f"{MARKER}-ERROR reason=readback", file=sys.stderr)
            return 5
    except (OSError, UnicodeError, ValueError) as error:
        print(f"{MARKER}-ERROR reason={error.__class__.__name__}", file=sys.stderr)
        return 3
    print(f"{MARKER} applied have={wanted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

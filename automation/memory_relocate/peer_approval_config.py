"""The peer verifier's private config, written by the release instead of by hand.

The owner asked that an approved release needs no node work (2026-10-14 22:29). The peer's
memory-choice watcher reads exactly one key, ``agent_chat_channel_id``, from a peer-owned
0600 file kept apart from the peer's general interop config (adding the key there would make
the peer gateway start answering in that channel). The deployer reads the id from the agent's
config (``--print-channel``, run as the agent) and writes it as the peer (``--channel``).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Final

from .choice_files import atomic_text

_CHANNEL: Final = re.compile(r"[0-9]{17,20}\Z")
CONFIG_PATH: Final = Path.home() / ".hermes" / "memory-relocate-peer" / "approval-config.json"
_AGENT_INTEROP: Final = Path.home() / ".hermes" / "interop" / "config.json"


def agent_channel(interop: Path = _AGENT_INTEROP) -> str:
    value = json.loads(interop.read_text(encoding="utf-8")).get("agent_chat_channel_id")
    if not isinstance(value, str) or not _CHANNEL.fullmatch(value):
        raise ValueError("agent_chat_channel_id is missing or malformed")
    return value


def write_config(channel: str, path: Path = CONFIG_PATH) -> bool:
    if not _CHANNEL.fullmatch(channel):
        raise ValueError("channel id is malformed")
    text = json.dumps({"agent_chat_channel_id": channel}, sort_keys=True) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text and (path.stat().st_mode & 0o777) == 0o600:
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = os.umask(0o077)
    try:
        atomic_text(path, text)
    finally:
        _ = os.umask(previous)
    path.chmod(0o600)
    return True


def main(argv: list[str]) -> int:
    try:
        if argv == ["--print-channel"]:
            print(agent_channel())
            return 0
        if len(argv) == 2 and argv[0] == "--channel":
            changed = write_config(argv[1])
            print("PEER-APPROVAL-CONFIG " + ("written" if changed else "unchanged"))
            return 0
        print("usage: --print-channel | --channel <id>", file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(f"PEER-APPROVAL-CONFIG-REFUSED type={type(error).__name__}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

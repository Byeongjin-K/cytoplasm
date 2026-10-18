"""Release-host secret transport. Only a credential digest crosses argv or approval JSON."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

def release_switch(home: Path) -> bool:
    try:
        value = (home / ".hermes/release-flags/notice-bot").read_bytes()
    except FileNotFoundError:
        return False
    if value != b"yoodori":
        raise ValueError("invalid notice-bot switch")
    return True


def source_token(home: Path) -> str:
    """The existing account wrapper owns the account name; no private home is in code."""
    result = subprocess.run(
        [str(home / ".local/bin/yoodori-do"), "sh", "-c", 'exec cat "$HOME/.yoodori/dori.env"'],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode:
        raise ValueError("source unavailable")
    value = ""
    for line in result.stdout.splitlines():
        key, sep, candidate = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() == "DORI_DISCORD_TOKEN":
            value = candidate.strip().strip("'\"")
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError("source invalid")
    return value


def switch_digest(home: Path) -> str:
    if not release_switch(home):
        return "off"
    return hashlib.sha256(source_token(home).encode()).hexdigest()


def deploy(repo: Path, host: str, runtime: str, head: str, digest: str, channel: str) -> int:
    """Authorize before every node write; send captured bytes only on SSH stdin."""
    if digest != "off" and (re.fullmatch(r"[0-9a-f]{64}", digest) is None
                            or re.fullmatch(r"[1-9][0-9]{16,19}", channel) is None):
        raise ValueError("invalid release mode")
    payload = ""
    if digest != "off":
        token = source_token(Path.home())
        if hashlib.sha256(token.encode()).hexdigest() != digest:
            raise ValueError("source changed after plan")
        authorized = subprocess.run(
            [str(repo / "automation/release_approval_remote.sh"), "authorize-notice-bot",
             "--hash", digest, "--head", head], check=False,
        )
        if authorized.returncode:
            print("NOTICE-BOT-UNAUTHORIZED", file=sys.stderr)
            return 4
        payload = json.dumps({"token": token, "channel_id": channel})
    from automation.node_config import load_node_config

    config = load_node_config()
    for account in (config.agent_account, config.peer_account, config.ops_account):
        command = shlex.join(("sudo", "-n", "-u", account, "-H", "env", f"PYTHONPATH={runtime}",
                              "python3", "-B", "-m", "automation.notice_bot", "install",
                              "--digest", digest, "--channel", channel))
        result = subprocess.run(["ssh", host, command], input=payload, text=True,
                                capture_output=True, check=False, timeout=60)
        # Remote errors can echo stdin: never relay either stream.
        if result.returncode:
            print(f"NOTICE-BOT-DEPLOY-FAIL account={account}", file=sys.stderr)
            return 4
    return 0


def observe(runtime: Path, digest: str, channel: str) -> dict[str, str]:
    """Read-back all accounts before a clean receipt; no secret bytes are returned."""
    from automation.node_config import load_node_config

    config = load_node_config()
    states: dict[str, str] = {}
    for account in (config.agent_account, config.peer_account, config.ops_account):
        result = subprocess.run(
            ("sudo", "-n", "-u", account, "-H", "env", f"PYTHONPATH={runtime}",
             "python3", "-B", "-m", "automation.notice_bot", "installed",
             "--digest", digest, "--channel", channel),
            capture_output=True, text=True, check=False, timeout=30,
        )
        states[account] = "ok" if result.returncode == 0 and result.stdout.strip() == "NOTICE-BOT installed=yes" else "drift"
    return states


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.notice_bot_release")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("digest")
    apply = commands.add_parser("apply")
    for name in ("repo", "host", "runtime", "head", "digest", "channel"):
        apply.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "digest":
            print(switch_digest(Path.home()))
            return 0
        return deploy(Path(args.repo), args.host, args.runtime, args.head, args.digest, args.channel)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("NOTICE-BOT-REFUSED reason=source-or-transport", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

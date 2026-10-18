"""One credential resolver for node Discord clients; release secrets never leave this boundary."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from automation.node_config import NodeConfig, NodeConfigError, load_node_config

TOKEN_KEY: Final = "DISCORD_BOT_TOKEN"
OVERRIDE: Final = ".hermes/notice-bot.json"
NOTICE: Final = "발신 봇: 유돌(덮어씀)"
SURFACE: Final = "notice-bot:yoodori"
CARD_LINE: Final = "발신 봇 바꾸기: 노드 알림·승인 카드를 유돌 봇으로 보냄(Agent·Peer·Ops 봇은 아무것도 보내지 않음)"
FAULT_PREFIX: Final = "fault-channel:"
FAULT_CARD_LINE: Final = "고장 알림은 #dori-pair 로(유돌 먼저)"


@dataclass(frozen=True, slots=True)
class Override:
    token: str = field(repr=False)
    channel_id: str
    fault_channel_id: str | None = None

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.token.encode()).hexdigest()


def node_config() -> NodeConfig:
    path = os.environ.get("HEALTHCHECK_NODE_CONFIG_PATH")
    return load_node_config(Path(path) if path else None)


def override_path(home: Path | None = None, config: NodeConfig | None = None) -> Path:
    root, node = home or Path.home(), config or node_config()
    return node.private_root / "notice-bot.json" if root == node.ops_home else root / OVERRIDE


def parse_override(text: str) -> Override:
    raw = json.loads(text)
    if (not isinstance(raw, dict)
            or set(raw) not in ({"token", "channel_id"}, {"token", "channel_id", "fault_channel_id"})):
        raise ValueError("invalid override shape")
    token, channel = raw["token"], raw["channel_id"]
    if (not isinstance(token, str) or not token or len(token) > 4096
            or any(char in token for char in "\r\n\0")
            or not isinstance(channel, str) or re.fullmatch(r"[1-9][0-9]{16,19}", channel) is None):
        raise ValueError("invalid override fields")
    fault = raw.get("fault_channel_id")
    if "fault_channel_id" in raw:
        fault_surface(fault)
    return Override(token, channel, fault)


def read_override(home: Path | None = None) -> Override | None:
    """Missing is legacy; malformed/unreadable is a masked warning, never a secret."""
    try:
        path = override_path(home)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.geteuid() or info.st_size > 8192):
                raise ValueError("override metadata")
            return parse_override(stream.read(8193))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError, NodeConfigError):
        print("NOTICE-BOT-WARN reason=override-unreadable-or-invalid", file=sys.stderr)
        return None


def resolve_token(
    fallback: str | None = None, *, home: Path | None = None,
    environ: Mapping[str, str] | None = None, required: bool = False,
) -> str:
    """Override first; otherwise preserve the caller's old token or environment value."""
    override = read_override(home)
    if override is not None:
        return override.token
    if fallback is not None:
        return fallback
    environment = os.environ if environ is None else environ
    return environment[TOKEN_KEY] if required else environment.get(TOKEN_KEY, "")


def file_token(path: Path, *, shell: bool = False) -> str:
    """Legacy file parsing for callers that already loaded a fallback file."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if shell:
            line = line.strip().removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        if sep and (key.strip() if shell else key) == TOKEN_KEY:
            if shell:
                value = value.split(" #", 1)[0]
            return value.strip().strip("'\"").strip() if shell else value.strip()
    return ""


def notice_channel(home: Path | None = None) -> str | None:
    override = read_override(home)
    return override.channel_id if override is not None else None


def fault_surface(channel: str) -> tuple[str, str]:
    """The installation-specific fault destination is an approved release effect."""
    if not isinstance(channel, str) or re.fullmatch(r"[1-9][0-9]{16,19}", channel) is None:
        raise ValueError("invalid fault channel")
    return FAULT_PREFIX + channel, hashlib.sha256(channel.encode()).hexdigest()


def fault_channel(home: Path | None = None) -> str | None:
    """One fault resolver for all senders; absence preserves each legacy destination."""
    override = read_override(home)
    return override.fault_channel_id if override is not None else None


def installed(digest: str, channel: str, fault: str = "off") -> bool:
    path = override_path()
    if digest == "off":
        return not path.exists() and not path.is_symlink()
    override = read_override()
    return override is not None and (
        override.digest, override.channel_id, override.fault_channel_id or "off"
    ) == (digest, channel, fault)


def install(digest: str, channel: str, fault: str = "off") -> None:
    path = override_path()
    if digest == "off":
        path.unlink(missing_ok=True)
        return
    value = parse_override(sys.stdin.read(8193))
    if (value.digest, value.channel_id, value.fault_channel_id or "off") != (digest, channel, fault):
        raise ValueError("stdin differs from approved identity")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".notice-bot-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            payload = {"token": value.token, "channel_id": value.channel_id}
            if value.fault_channel_id is not None:
                payload["fault_channel_id"] = value.fault_channel_id
            stream.write(json.dumps(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    if not installed(digest, channel, fault):
        raise ValueError("override readback differs")


def check(expected: str) -> int:
    from urllib.request import Request, urlopen

    token = resolve_token()
    if not token:
        home = Path.home()
        paths = (home / ".hermes/.env", home / ".env.secrets")
        if home == node_config().ops_home:
            paths = (Path("/etc/autophagy/repair-approval.env"), *paths)
        for path in paths:
            try:
                token = file_token(path, shell=True)
            except OSError:
                continue
            if token:
                break
    if not token:
        raise ValueError("credential absent")
    request = Request("https://discord.com/api/v10/users/@me",
                      headers={"Authorization": f"Bot {token}", "User-Agent": "Autophagy sender check"})
    with urlopen(request, timeout=30) as response:  # noqa: S310
        raw = json.loads(response.read())
    if not isinstance(raw, dict):
        raise ValueError("identity not an object")
    bot_id = raw.get("id")
    if raw.get("bot") is not True or not isinstance(bot_id, str) or not bot_id.isdecimal():
        raise ValueError("identity not a bot")
    matches = bot_id == expected
    print(f"NOTICE-BOT bot_id={bot_id} match={'yes' if matches else 'no'}")
    return 0 if matches else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.notice_bot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    identity = sub.add_parser("check")
    identity.add_argument("--expected-id", required=True)
    for command in ("install", "installed"):
        mode = sub.add_parser(command)
        mode.add_argument("--digest", required=True)
        mode.add_argument("--channel", required=True)
        mode.add_argument("--fault-channel", default="off")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            if read_override() is not None:
                print(f"[PASS] {NOTICE}")
            return 0
        if args.command == "check":
            return check(args.expected_id)
        if args.digest != "off" and re.fullmatch(r"[0-9a-f]{64}", args.digest) is None:
            raise ValueError("invalid digest")
        if args.command == "install":
            install(args.digest, args.channel, args.fault_channel)
        ok = installed(args.digest, args.channel, args.fault_channel)
        print(f"NOTICE-BOT installed={'yes' if ok else 'no'}")
        return 0 if ok else 1
    except (OSError, ValueError, UnicodeError, NodeConfigError):
        print("NOTICE-BOT-FAIL reason=unavailable-or-invalid", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

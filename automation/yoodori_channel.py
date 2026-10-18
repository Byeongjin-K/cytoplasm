"""Release-owned chat/notice routing; no node reads a release-host flag."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from automation.hermes_agent_chat_ignore import write_config
from automation.hermes_memory_config import ConfigShapeError
from automation.node_config import NodeConfigError

ALGORITHM: Final = "yoodori-channel-v1"
MARKER: Final = "YOODORI-CHANNEL"
CHAT: Final = "agent_chat_channel_id"
NOTICE: Final = "owner_notice_channel_id"
BEFORE: Final = "_before_release"
# Logical declaration destination; the module resolves it against node.private_root, not HOME.
OVERRIDE: Final = "private/channel-override.json"
_ID: Final = re.compile(r"[1-9][0-9]{16,19}")


def channel_value(value: str) -> str:
    if value != "off" and _ID.fullmatch(value) is None:
        raise ConfigShapeError("YOODORI-CHANNEL-REFUSED: expected a decimal channel id only")
    return value


def release_switch(home: Path) -> str | None:
    try:
        value = (home / ".hermes/release-flags/yoodori-channel").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    if value == "off":
        raise ConfigShapeError("YOODORI-CHANNEL-REFUSED: expected a decimal channel id only")
    return channel_value(value)


def _members(text: str) -> dict[str, tuple[int, int, int]]:
    """Top-level value spans and preceding separators; nested values remain opaque."""
    decoder = json.JSONDecoder()
    if not isinstance(json.loads(text), dict):
        raise ConfigShapeError("interop config must be an object")
    spans: dict[str, tuple[int, int, int]] = {}
    cursor = text.index("{") + 1
    separator = cursor
    while True:
        cursor += len(text[cursor:]) - len(text[cursor:].lstrip())
        if text[cursor] == "}":
            return spans
        key, end = decoder.raw_decode(text, cursor)
        if key in spans:
            raise ConfigShapeError("duplicate interop config key")
        cursor = text.index(":", end) + 1
        cursor += len(text[cursor:]) - len(text[cursor:].lstrip())
        _, end = decoder.raw_decode(text, cursor)
        spans[key] = (separator, cursor, end)
        cursor = end + len(text[end:]) - len(text[end:].lstrip())
        if text[cursor] == "}":
            return spans
        separator, cursor = cursor, cursor + 1


def _set(text: str, key: str, raw: str | None) -> str:
    spans = _members(text)
    if key not in spans:
        if raw is None:
            return text
        close = text.rindex("}")
        comma = ", " if spans else ""
        return text[:close] + f"{comma}{json.dumps(key)}: {raw}" + text[close:]
    separator, start, end = spans[key]
    if raw is not None:
        return text[:start] + raw + text[end:]
    if separator != text.index("{") + 1:
        return text[:separator] + text[end:]
    stop = end + len(text[end:]) - len(text[end:].lstrip())
    return text[:separator] + text[stop + 1 if text[stop] == "," else stop:]


def original_chat(path: Path) -> tuple[str, str | None]:
    text = path.read_bytes().decode("utf-8")
    _members(text)
    raw = json.loads(text)
    current, previous = raw.get(CHAT), raw.get(CHAT + BEFORE)
    for value in (current, previous) if previous is not None else (current,):
        if type(value) not in (str, int) or re.fullmatch(r"[1-9][0-9]*", str(value)) is None:
            raise ConfigShapeError("invalid interop chat value")
    return str(current), str(previous) if previous is not None else None


def wanted_identity(*, yoodori_channel: str = "off") -> str:
    return f"yoodori_channel={channel_value(yoodori_channel)}"


def _keys(role: str) -> tuple[str, ...]:
    return (NOTICE,) if role == "peer" else (CHAT, NOTICE)


def identity(text: str, role: str) -> str:
    spans = _members(text)
    raw = json.loads(text)
    owned = [key for key in _keys(role) if key + BEFORE in spans]
    if not owned:
        return wanted_identity()
    values = [raw.get(key) for key in owned]
    if (len(owned) != len(_keys(role)) or len(set(values)) != 1
            or any(type(value) is not str or _ID.fullmatch(value) is None for value in values)):
        return "yoodori_channel=drift"
    return wanted_identity(yoodori_channel=values[0])


def apply_channel(text: str, target: str, role: str = "agent") -> str:
    channel_value(target)
    spans = _members(text)
    raw = json.loads(text)
    keys = _keys(role)
    if target == "off":
        for key in reversed(keys):
            saved = key + BEFORE
            if saved not in _members(text):
                continue
            _, start, end = _members(text)[saved]
            original = text[start:end]
            text = _set(text, saved, None)
            text = _set(text, key, None if json.loads(original) == {"absent": True} else original)
        return text
    if role == "agent" and CHAT not in spans:
        raise ConfigShapeError("agent_chat_channel_id is required for a move")
    for key in keys:
        saved = key + BEFORE
        if saved not in spans:
            original = '{"absent": true}'
            if key in spans:
                _, start, end = spans[key]
                original = text[start:end]
            text = _set(text, key, json.dumps(target))
            text = _set(text, saved, original)
            spans = _members(text)
        else:
            if not isinstance(raw[saved], (str, int, dict)) or isinstance(raw[saved], bool):
                raise ConfigShapeError("invalid release restore value")
            text = _set(text, key, json.dumps(target))
    return text


def _ops_before() -> str:
    """Read existing root configuration without editing it; values are restore/audit only."""
    try:
        document = json.loads(Path(os.environ.get("INTEROP_CONFIG", "/etc/autophagy/interop-ops.json"))
                              .read_text(encoding="utf-8"))
    except FileNotFoundError:
        document = {}
    notice = os.environ.get("OWNER_NOTICE_CHANNEL_ID")
    if notice is None:
        try:
            for line in Path("/etc/autophagy/repair-approval.env").read_text(encoding="utf-8").splitlines():
                if line.startswith("OWNER_NOTICE_CHANNEL_ID="):
                    notice = line.partition("=")[2].strip().strip("'\"")
        except FileNotFoundError:
            pass
    return json.dumps({CHAT: document.get(CHAT), NOTICE: notice})


def _check_guild(text: str, target: str, home: Path, role: str) -> None:
    """Only the release writer performs network validation, when this account pins a guild."""
    from urllib.request import Request, urlopen

    raw = json.loads(text)
    guild = next((raw[key] for key in ("owner_guild_id", "personal_guild_id", "guild_id")
                  if raw.get(key)), None)
    if guild is None:
        return
    from automation.notice_bot import file_token, resolve_token

    token = resolve_token(home=home)
    if not token:
        paths = [home / ".env.secrets"]
        if role == "ops":
            paths.append(Path("/etc/autophagy/repair-approval.env"))
        for path in paths:
            try:
                token = file_token(path, shell=True)
            except FileNotFoundError:
                continue
            if token:
                break
    if not token:
        raise ConfigShapeError("guild validation requires the account bot credential")
    request = Request(
        f"https://discord.com/api/v10/channels/{target}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "Autophagy release channel check"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        channel = json.loads(response.read())
    if channel.get("guild_id") != str(guild) or channel.get("type") != 0:
        raise ConfigShapeError("channel must be a text channel of the configured owner guild")


def main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.yoodori_channel")
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ("show", "check", "apply", "release-switch", "override-status"):
        mode.add_argument(f"--{name}", action="store_true")
    parser.add_argument("--yoodori-channel", default="off", type=channel_value)
    parser.add_argument("--role", choices=("agent", "peer", "ops"), default="agent")
    args = parser.parse_args(argv)
    root = home or Path.home()
    try:
        if args.release_switch:
            print(release_switch(root) or "")
            return 0
        if args.override_status:
            from automation.interop.approval_directory import CHANNEL_OVERRIDE_NOTICE, channel_override

            if channel_override(root) is not None:
                print(f"[PASS] {CHANNEL_OVERRIDE_NOTICE}")
            return 0
        if args.role == "ops":
            from automation.interop.approval_directory import channel_override_path

            path = channel_override_path()
        else:
            path = root / ".hermes/interop/config.json"
        exists = path.exists()
        text = path.read_bytes().decode("utf-8") if exists else "{}"
        current = identity(text, args.role)
        metadata_drift = False
        if args.role == "ops" and exists:
            metadata_drift = path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o777 != 0o600
            if metadata_drift:
                current = "yoodori_channel=drift"
        wanted = wanted_identity(yoodori_channel=args.yoodori_channel)
        if args.show:
            print(f"{MARKER} have={current}")
            return 0
        if args.check:
            print(f"{MARKER} have={current} want={wanted}")
            return 0 if current == wanted else 1
        if args.role == "ops" and args.yoodori_channel == "off":
            path.unlink(missing_ok=True)
        else:
            if args.role == "ops" and not exists:
                text = _ops_before()
            after = apply_channel(text, args.yoodori_channel, args.role)
            if after != text or metadata_drift:
                if args.yoodori_channel != "off":
                    source = Path(os.environ.get("INTEROP_CONFIG", "/etc/autophagy/interop-ops.json"))
                    checked = source.read_text(encoding="utf-8") if args.role == "ops" and source.is_file() else text
                    _check_guild(checked, args.yoodori_channel, root, args.role)
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                write_config(path, after)
                os.chmod(path, 0o600)
                if path.read_bytes() != after.encode() or identity(after, args.role) != wanted:
                    print(f"{MARKER}-ERROR reason=readback", file=sys.stderr)
                    return 5
        print(f"{MARKER} have={wanted}")
        return 0
    except (OSError, UnicodeError, ValueError, NodeConfigError) as error:
        print(f"{MARKER}-ERROR reason={type(error).__name__}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

"""Account-local agent-chat cutover; edit only discord.ignored_channels.

The release host alone reads the opt-in switch. The node receives an explicit
mode through memory_provider.agent_config, alongside the existing memory profile.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.hermes_memory_config import ConfigShapeError

ALGORITHM: Final = "hermes-agent-chat-ignored-v1"
MARKER: Final = "HERMES-AGENT-CHAT-IGNORED"
_ID: Final = re.compile(r"[1-9][0-9]*")
_KEY: Final = re.compile(r"""^( *)([A-Za-z_][A-Za-z0-9_-]*|'[A-Za-z_]+'|"[A-Za-z_]+"): *(.*)$""")
_ATOM: Final = r"""(?:[1-9][0-9]*|'[1-9][0-9]*'|"[1-9][0-9]*")"""
_CSV: Final = re.compile(rf"{_ATOM}(?: *, *{_ATOM})*")
_ITEM: Final = re.compile(rf"^( *)- +({_ATOM}) *$")


def release_switch(home: Path) -> str | None:
    """Absent means off; malformed or unreadable never grants a cutover."""
    try:
        value = (home / ".hermes/release-flags/dori-agent-chat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    if re.fullmatch(r"[0-9]+", value) is None:
        raise ConfigShapeError("DORI-SWITCH-REFUSED: expected a decimal bot id only")
    return value


@dataclass(frozen=True, slots=True)
class Ignored:
    lines: tuple[str, ...]
    head: int | None
    indent: str
    key: int | None
    items: tuple[int, ...]
    channels: tuple[str, ...]


def agent_chat_channel(root: Path) -> str:
    """Read the account's channel; absent, malformed and unreadable all fail closed."""
    raw = json.loads((root / "interop/config.json").read_bytes().decode("utf-8"))
    value = raw.get("agent_chat_channel_id") if isinstance(raw, dict) else None
    if type(value) not in (str, int) or not _ID.fullmatch(str(value)):
        raise ConfigShapeError("agent_chat_channel_id must be a positive decimal id")
    return str(value)


def _value(line: str) -> str:
    return line.split(" #", 1)[0].rstrip("\r\n ")


def read_ignored(text: str, *, key_name: str = "ignored_channels") -> Ignored:
    """Parse block mappings and decimal channel lists, refusing ambiguous YAML."""
    lines = tuple(text.splitlines(keepends=True))
    roots: dict[str, int] = {}
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#") or line.startswith(" "):
            continue
        match = _KEY.fullmatch(_value(line))
        if match is None:
            previous = next(reversed(roots), None)
            if (line.startswith("- ") and previous is not None and previous != "discord"
                    and not _value(lines[roots[previous]]).split(":", 1)[1].strip()):
                continue
            raise ConfigShapeError("unsupported top-level mapping")
        key = match[2].strip("'\"")
        if key in roots:
            raise ConfigShapeError("duplicate top-level key")
        roots[key] = index
    head = roots.get("discord")
    if head is None:
        return Ignored(lines, None, "  ", None, (), ())
    if _value(lines[head]).split(":", 1)[1].strip():
        raise ConfigShapeError("discord must be a block mapping")
    end = min((i for i in roots.values() if i > head), default=len(lines))
    active = [i for i in range(head + 1, end) if lines[i].strip() and not lines[i].lstrip().startswith("#")]
    if any("\t" in lines[i][:len(lines[i]) - len(lines[i].lstrip())] for i in active):
        raise ConfigShapeError("tabs in discord indentation")
    indent = len(lines[active[0]]) - len(lines[active[0]].lstrip()) if active else 2
    if active and (_KEY.fullmatch(_value(lines[active[0]])) is None or
                   any(len(lines[i]) - len(lines[i].lstrip()) < indent for i in active)):
        raise ConfigShapeError("discord must have consistent mapping indentation")
    found: dict[str, int] = {}
    for index in active:
        line = lines[index]
        if len(line) - len(line.lstrip()) != indent:
            continue
        if line.lstrip().startswith("<<:"):
            raise ConfigShapeError("discord merge keys are unsupported")
        match = _KEY.fullmatch(_value(line))
        if match is None:
            if not line.lstrip().startswith("- "):
                raise ConfigShapeError("unsupported discord key")
            continue
        key = match[2].strip("'\"")
        if key in found:
            raise ConfigShapeError("duplicate discord key")
        found[key] = index
    key = found.get(key_name)
    if key is None:
        return Ignored(lines, head, " " * indent, None, (), ())
    stop = min((i for i in found.values() if i > key), default=end)
    raw = _value(lines[key]).split(":", 1)[1].strip()
    items: list[int] = []
    channels: list[str] = []
    for index in range(key + 1, stop):
        if not lines[index].strip() or lines[index].lstrip().startswith("#"):
            continue
        item = _ITEM.fullmatch(_value(lines[index]))
        if raw or item is None or len(item[1]) < indent:
            raise ConfigShapeError("ignored_channels must contain only decimal ids")
        if items and len(item[1]) != len(lines[items[0]]) - len(lines[items[0]].lstrip()):
            raise ConfigShapeError("inconsistent list indentation")
        items.append(index)
        channels.append(item[2].strip("'\""))
    if raw:
        inner = raw
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
        elif raw[:1] in ("'", '"') and raw[-1:] == raw[:1]:
            inner = raw[1:-1]
        if inner and not _CSV.fullmatch(inner):
            raise ConfigShapeError("unsupported ignored_channels value")
        channels = [part.strip().strip("'\"") for part in inner.split(",")] if inner else []
    return Ignored(lines, head, " " * indent, key, tuple(items), tuple(channels))


def identity(text: str, channel: str) -> str:
    return f"agent_chat_ignored={'yes' if channel in read_ignored(text).channels else 'no'}"


def wanted_identity() -> str:
    return "agent_chat_ignored=yes"


def apply_ignore(
    text: str, channel: str, *, remove: bool = False, key_name: str = "ignored_channels",
) -> str:
    """Keep all bytes outside the selected list, including comments and line endings."""
    if not _ID.fullmatch(channel):
        raise ConfigShapeError("invalid channel id")
    before = read_ignored(text, key_name=key_name)
    present = channel in before.channels
    if present != remove:
        return text
    lines = list(before.lines)
    eol = "\r\n" if "\r\n" in text else "\n"
    if before.key is None:
        if before.head is None:
            prefix = text + (eol if text and not text.endswith("\n") else "")
            return prefix + f"discord:{eol}  {key_name}: '{channel}'{eol}"
        lines.insert(before.head + 1, f"{before.indent}{key_name}: '{channel}'{eol}")
        if not lines[before.head].endswith("\n"):
            lines[before.head] += eol
        return "".join(lines)
    if before.items:
        if remove:
            for index, value in reversed(tuple(zip(before.items, before.channels, strict=True))):
                if value == channel:
                    del lines[index]
        else:
            last = before.items[-1]
            indent = lines[last][:len(lines[last]) - len(lines[last].lstrip())]
            if not lines[last].endswith("\n"):
                lines[last] += eol
            lines.insert(last + 1, f"{indent}- '{channel}'{eol}")
    else:
        values = [value for value in before.channels if value != channel] if remove else [*before.channels, channel]
        line = lines[before.key]
        content = line.rstrip("\r\n")
        body, sep, comment = content.partition(" #")
        prefix = body[:body.index(":") + 1]
        suffix = line[len(content):]
        rendered = "'" + ",".join(values) + "'" if values else "[]"
        lines[before.key] = f"{prefix} {rendered}{sep}{comment}{suffix}"
    return "".join(lines)


def write_config(path: Path, text: str) -> Path:
    """One backup and atomic replacement, shared by the standalone and composite CLI."""
    backup = path.with_name(f"{path.name}.bak-agent-profile-{time.time_ns()}")
    if path.exists():
        shutil.copy2(path, backup)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.agent-profile-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return backup


def main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.hermes_agent_chat_ignore")
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ("show", "check", "apply", "remove", "release-switch"):
        mode.add_argument(f"--{name}", action="store_true")
    args = parser.parse_args(argv)
    if args.release_switch:
        try:
            print(release_switch(home or Path.home()) or "")
        except (OSError, UnicodeError, ValueError) as error:
            print(f"DORI-SWITCH-REFUSED reason={error.__class__.__name__}", file=sys.stderr)
            return 3
        return 0
    root = (home or Path.home()) / ".hermes"
    path = root / "config.yaml"
    try:
        channel = agent_chat_channel(root)
        text = path.read_bytes().decode("utf-8")
        current = identity(text, channel)
        if args.show:
            print(f"{MARKER} have={current}")
            return 0
        wanted = "agent_chat_ignored=no" if args.remove else wanted_identity()
        if args.check:
            print(f"{MARKER} have={current} want={wanted}")
            return 0 if current == wanted else 1
        if current == wanted:
            print(f"{MARKER} unchanged have={current}")
            return 0
        after = apply_ignore(text, channel, remove=args.remove)
        write_config(path, after)
        readback = path.read_bytes().decode("utf-8")
        if readback != after or identity(readback, channel) != wanted:
            print(f"{MARKER}-ERROR reason=readback", file=sys.stderr)
            return 5
    except (OSError, UnicodeError, ValueError) as error:
        print(f"{MARKER}-ERROR reason={error.__class__.__name__}", file=sys.stderr)
        return 3
    print(f"{MARKER} applied have={wanted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""agent-chat 채널을 agent 게이트웨이의 무멘션 응답 채널로 등록한다(2026-10-02).

Hermes 는 ``discord.free_response_channels`` 에 든 채널만 @ 없이 답한다. 그 등록은 설치 문서의 수동
단계였다. 정본은 ``~/.hermes/interop/config.json`` 의 ``agent_chat_channel_id`` 이고, 이 모듈은 그 id 가
같은 계정 ``~/.hermes/config.yaml`` 최상위 ``discord:`` 블록에 **정확히 한 번** 들도록 빠졌을 때만 한 줄을
고친다(다른 줄·순서·주석 보존, ``config.yaml.bak-free-response-<UTC>`` 백업 뒤 원자 교체). ignored 에 든
채널은 넣지 않고(Hermes 에서 ignored 가 우선), 키가 없는 계정(peer)은 건드리지 않는다. 실행 위치는 게이트웨이
드롭인의 ``ExecStartPre=-…`` 이고 doctor 가 같은 판정을 읽기 전용으로 쓴다.

    python3 -m automation.interop.free_response [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

KEY: Final = "free_response_channels"
IGNORED_KEY: Final = "ignored_channels"
_BLOCK: Final = "discord"
_KEY_LINE: Final = re.compile(r"^(?P<indent>[ \t]+)(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?P<rest>.*?)(?P<eol>\r?\n?)$")
_ITEM_LINE: Final = re.compile(r"^(?P<indent>[ \t]+)-\s*(?P<value>.*?)(?P<eol>\r?\n?)$")


class State(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    IGNORED = "ignored"
    NO_CHANNEL = "no-channel"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class Decision:
    state: State
    channel: str
    text: str | None = None
    detail: str = ""


def _strip(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1].strip()
    return value


def _scalar(rest: str) -> str:
    """`key: value  # comment` 의 value. 채널 id 는 숫자라 따옴표 안의 # 를 걱정하지 않는다."""
    value = rest.split(" #", 1)[0].strip()
    return value


def _items(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    else:
        value = _strip(value)
    return [item for item in (_strip(part) for part in value.split(",")) if item]


@dataclass(frozen=True, slots=True)
class _Block:
    start: int
    end_exclusive: int


def _discord_block(lines: list[str]) -> _Block | None | str:
    for index, line in enumerate(lines):
        if not line.startswith(f"{_BLOCK}:"):
            continue
        tail = line[len(_BLOCK) + 1:].split("#", 1)[0].strip()
        if tail:
            return "inline discord mapping"
        end = index + 1
        while end < len(lines):
            current = lines[end]
            if current.strip() and not current[0].isspace() and not current.lstrip().startswith("#"):
                break
            end += 1
        return _Block(index, end)
    return None


@dataclass(frozen=True, slots=True)
class _Key:
    line: int
    indent: str
    inline_value: str
    list_lines: tuple[int, ...]


def _find_key(lines: list[str], block: _Block, key: str) -> _Key | None:
    for index in range(block.start + 1, block.end_exclusive):
        match = _KEY_LINE.match(lines[index])
        if not match or match["key"] != key:
            continue
        indent = match["indent"]
        value = _scalar(match["rest"])
        items: list[int] = []
        if not value:
            follow = index + 1
            while follow < block.end_exclusive:
                item = _ITEM_LINE.match(lines[follow])
                if item and len(item["indent"]) >= len(indent):
                    items.append(follow)
                    follow += 1
                    continue
                if not lines[follow].strip() or lines[follow].lstrip().startswith("#"):
                    follow += 1
                    continue
                break
        return _Key(index, indent, value, tuple(items))
    return None


def _values(lines: list[str], found: _Key | None) -> list[str]:
    if found is None:
        return []
    if found.list_lines:
        values: list[str] = []
        for index in found.list_lines:
            item = _ITEM_LINE.match(lines[index])
            if item:
                values.extend(_items(_scalar(item["value"])))
        return values
    return _items(found.inline_value)


def _child_indent(lines: list[str], block: _Block) -> str:
    for index in range(block.start + 1, block.end_exclusive):
        match = _KEY_LINE.match(lines[index])
        if match:
            return match["indent"]
    return "  "


def _eol(lines: list[str]) -> str:
    return "\r\n" if lines and lines[0].endswith("\r\n") else "\n"


def _with_channel(lines: list[str], block: _Block | None, found: _Key | None, channel: str) -> str:
    out = list(lines)
    eol = _eol(lines)
    if block is None:
        if out and not out[-1].endswith("\n"):
            out[-1] += eol
        out.append(f"{_BLOCK}:{eol}")
        out.append(f"  {KEY}: '{channel}'{eol}")
        return "".join(out)
    if found is None:
        out.insert(block.start + 1, f"{_child_indent(lines, block)}{KEY}: '{channel}'{eol}")
        return "".join(out)
    if found.list_lines:
        last = found.list_lines[-1]
        item = _ITEM_LINE.match(lines[last])
        indent = item["indent"] if item else found.indent + "  "
        line_eol = (item["eol"] if item else "") or eol
        if not out[last].endswith("\n"):
            out[last] += line_eol
        out.insert(last + 1, f"{indent}- '{channel}'{line_eol}")
        return "".join(out)
    match = _KEY_LINE.match(lines[found.line])
    assert match is not None
    rest = match["rest"]
    comment = ""
    if " #" in rest:
        rest, comment = rest.split(" #", 1)
        comment = " #" + comment
    value = rest.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        new_value = f"[{inner}, '{channel}']" if inner else f"['{channel}']"
    elif len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        inner = value[1:-1].strip()
        new_value = f"{value[0]}{inner},{channel}{value[0]}" if inner else f"{value[0]}{channel}{value[0]}"
    elif value in ("", "null", "~"):
        new_value = f"'{channel}'"
    else:
        new_value = f"{value},{channel}"
    out[found.line] = f"{match['indent']}{KEY}: {new_value}{comment}{match['eol'] or eol}"
    return "".join(out)


def configured_channels(text: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """(free_response, ignored) — 최상위 discord 블록이 없으면 빈 묶음, 해석할 수 없으면 None."""
    lines = text.splitlines(keepends=True)
    block = _discord_block(lines)
    if isinstance(block, str):
        return None
    if block is None:
        return (), ()
    return (tuple(_values(lines, _find_key(lines, block, KEY))),
            tuple(_values(lines, _find_key(lines, block, IGNORED_KEY))))


def decide(config_text: str, channel: str) -> Decision:
    """순수 판정. MISSING 이면 바꾼 뒤의 본문을 함께 돌려준다."""
    channel = channel.strip()
    if not channel:
        return Decision(State.NO_CHANNEL, "")
    lines = config_text.splitlines(keepends=True)
    block = _discord_block(lines)
    if isinstance(block, str):
        return Decision(State.UNSUPPORTED, channel, detail=block)
    found = None if block is None else _find_key(lines, block, KEY)
    ignored = [] if block is None else _values(lines, _find_key(lines, block, IGNORED_KEY))
    if channel in _values(lines, found):
        return Decision(State.PRESENT, channel)
    if channel in ignored:
        return Decision(State.IGNORED, channel)
    return Decision(State.MISSING, channel, _with_channel(lines, block, found, channel))


def agent_chat_channel(interop_config: Path) -> str:
    """interop config 의 agent_chat_channel_id. 파일·키가 없으면 빈 문자열(해당 없음)."""
    try:
        raw = json.loads(interop_config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    if not isinstance(raw, dict):
        raise ValueError("interop config is not a JSON object")
    value = raw.get("agent_chat_channel_id")
    return str(value).strip() if value not in (None, "") else ""


def _write(path: Path, text: str, now: datetime) -> Path:
    backup = path.with_name(f"{path.name}.bak-free-response-{now:%Y%m%dT%H%M%SZ}")
    shutil.copy2(path, backup)
    mode = path.stat().st_mode & 0o777
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return backup


_REPORT: Final = {
    State.PRESENT: (0, "FREE-RESPONSE-PRESENT: the agent-chat channel is already a free-response channel"),
    State.IGNORED: (0, "FREE-RESPONSE-SKIP: the agent-chat channel is in discord.ignored_channels (ignored wins); not added"),
    State.NO_CHANNEL: (0, "FREE-RESPONSE-SKIP: agent_chat_channel_id is not set for this account"),
}


def run(*, config: Path, interop: Path, apply: bool, now: datetime | None = None) -> tuple[int, str]:
    try:
        channel = agent_chat_channel(interop)
        text = config.read_text(encoding="utf-8") if channel else ""
    except (OSError, ValueError) as error:
        return 1, f"FREE-RESPONSE-FAIL: config unreadable ({type(error).__name__})"
    decision = decide(text, channel)
    if decision.state in _REPORT:
        return _REPORT[decision.state]
    if decision.state is State.UNSUPPORTED:
        return 1, f"FREE-RESPONSE-FAIL: cannot edit the discord block safely ({decision.detail})"
    if not apply:
        return 0, "FREE-RESPONSE-MISSING: the agent-chat channel is not a free-response channel (run with --apply)"
    assert decision.text is not None
    backup = _write(config, decision.text, now or datetime.now(UTC))
    if decide(config.read_text(encoding="utf-8"), channel).state is not State.PRESENT:
        return 1, f"FREE-RESPONSE-FAIL: read-back did not find the channel; backup {backup.name}"
    return 0, f"FREE-RESPONSE-ADDED: the agent-chat channel was added to discord.{KEY} (backup {backup.name})"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="빠졌으면 config.yaml 에 더한다")
    parser.add_argument("--config", type=Path, default=Path("~/.hermes/config.yaml"))
    parser.add_argument("--interop-config", type=Path,
                        default=Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")))
    args = parser.parse_args(argv)
    code, line = run(config=args.config.expanduser(), interop=args.interop_config.expanduser(), apply=args.apply)
    print(line, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""The 세션 tab's input: a 7-day session summary another host collects and copies next to the board.

The dashboard runs on the node, but the sessions it shows ran on a workstation. That host's
collector (``session_feed/collect.mjs``) writes ``sessions.json`` (metadata only: department and
theme labels, statuses, times, durations, counts; never lane keys, titles, prompts or paths) and
``session_feed/feed.sh`` copies it into the board root over SSH. This module reads that file
strictly; anything malformed is reported as unreadable instead of being half-shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

SESSIONS_FILE: Final = "sessions.json"

LANE_DEPTS: Final = ("ops", "assistant", "research", "web", "study", "infra", "etc")
# wait: parked on a person or a time (dori paused); block: its agent stopped on an error, a prompt or a closed window.
# hold is the name older collectors gave to wait and is read as wait.
LANE_STATES: Final = ("done", "run", "review", "wait", "block", "fail")
_LEGACY_STATES: Final = {"hold": "wait"}
# a lane's reason is one short line the collector already reduced to plain words; longer means it was not reduced
WHY_MAX: Final = 80
SERVICE_GROUPS: Final = ("watch", "web", "timer")
WINDOW_KINDS: Final = ("lead", "lane", "proj")


@dataclass(frozen=True, slots=True)
class Lane:
    dept: str
    theme: str
    st: str
    start: int
    end: int | None
    dur: int
    carried: bool
    why: str = ""
    since: int | None = None
    until: int | None = None


@dataclass(frozen=True, slots=True)
class Agent:
    platform: str
    fam: str
    start: int
    end: int
    dur: int
    calls: int
    live: bool


@dataclass(frozen=True, slots=True)
class Window:
    kind: str
    start: int


@dataclass(frozen=True, slots=True)
class Service:
    label: str
    group: str
    tag: str
    units: int
    st: str
    start: int | None
    restarts: int | None
    runs: int | None
    fails: int | None
    every: str
    last: int | None


@dataclass(frozen=True, slots=True)
class Sessions:
    """Times are epoch milliseconds; ``w0`` is 00:00 KST seven days before ``now``."""

    w0: int
    now: int
    lanes: tuple[Lane, ...]
    agents: tuple[Agent, ...]
    windows: tuple[Window, ...]
    services: tuple[Service, ...]


SessionsState = Sessions | Literal["missing", "unreadable"]


class _Bad(ValueError):
    pass


def _req(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise _Bad(key)
    return int(value)


def _opt(row: dict[str, object], key: str) -> int | None:
    return None if row.get(key) is None else _req(row, key)


def _text(row: dict[str, object], key: str, allowed: tuple[str, ...] = (), *, optional: bool = False) -> str:
    value = row.get(key, "" if optional else None)
    if not isinstance(value, str) or (allowed and value not in allowed) or (not optional and not value):
        raise _Bad(key)
    return value


def _state(row: dict[str, object]) -> str:
    value = row.get("st")
    return _text({"st": _LEGACY_STATES.get(value, value) if isinstance(value, str) else value}, "st", LANE_STATES)


def _why(row: dict[str, object]) -> str:
    value = _text(row, "why", optional=True)
    if len(value) > WHY_MAX or any(ch < " " for ch in value):
        raise _Bad("why")
    return value


def _rows(raw: dict[str, object], key: str) -> list[dict[str, object]]:
    rows = raw.get(key)
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise _Bad(key)
    return rows


def parse_sessions(raw: object) -> Sessions:
    """The collector's JSON as typed rows; raises ``ValueError`` naming the first bad field."""
    if not isinstance(raw, dict):
        raise _Bad("top level")
    lanes = tuple(
        Lane(_text(r, "dept", LANE_DEPTS), _text(r, "theme"), _state(r), _req(r, "start"),
             _opt(r, "end"), _req(r, "dur"), r.get("carried") is True, _why(r), _opt(r, "since"), _opt(r, "until"))
        for r in _rows(raw, "lanes")
    )
    agents = tuple(
        Agent(_text(r, "platform"), _text(r, "fam"), _req(r, "start"), _req(r, "end"), _req(r, "dur"),
              _req(r, "calls"), r.get("live") is True)
        for r in _rows(raw, "agents")
    )
    windows = tuple(Window(_text(r, "kind", WINDOW_KINDS), _req(r, "start")) for r in _rows(raw, "aoe"))
    services = tuple(
        Service(_text(r, "label"), _text(r, "group", SERVICE_GROUPS), _text(r, "tag"), _req(r, "units"),
                _text(r, "st"), _opt(r, "start"), _opt(r, "restarts"), _opt(r, "runs"), _opt(r, "fails"),
                _text(r, "every", optional=True), _opt(r, "last"))
        for r in _rows(raw, "services")
    )
    w0, now = _req(raw, "w0"), _req(raw, "now")
    if w0 >= now:
        raise _Bad("w0")
    return Sessions(w0, now, lanes, agents, windows, services)


def load_sessions(root: Path) -> SessionsState:
    path = root / SESSIONS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing"
    try:
        return parse_sessions(json.loads(text))
    except ValueError:
        return "unreadable"

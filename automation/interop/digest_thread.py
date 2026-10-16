"""One standard approval thread per morning digest (owner Q187, 2026-10-16).

The mail digest opens it through the standard per-request path (`approval_surface`
`resolve_new_binding` with a `RequestThread`), so its parent notice, name and big title
are the same as every other approval thread. Approval cards born from that digest -- a
reply draft made with a reaction or a reply, the calendar card of a mail's event --
post into it as replies under their mail card instead of opening threads of their own.

Mail and calendar run as separate processes, so the binding lives in one small state
file per KST day, written once under a file lock and read by both. Requests that did
not come from the digest never look here and keep their own threads.

The thread's mark follows its cards (`settle`): undecided cards -> the bell mark; no
undecided card and a newer digest exists -> closed as done and archived.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final

STATE_ENV: Final = "AUTOPHAGY_DIGEST_THREAD_DIR"
#: Days of digest threads whose mark is still followed; older ones are left as they are.
KEEP_DAYS: Final = 7
#: Leading mark while a card in the thread still waits for the owner's decision.
PENDING_MARK: Final = "🔔 "
MARK_PENDING: Final = "pending"
MARK_DONE: Final = "done"
MARK_NONE: Final = ""


def state_dir() -> Path:
    configured = os.environ.get(STATE_ENV)
    return Path(configured).expanduser() if configured else Path.home() / ".hermes" / "digest-threads"


@dataclass(frozen=True, slots=True)
class DigestThread:
    """The stored binding of one day's digest thread (a thread under the owner channel)."""

    day: str
    channel_id: str
    surface: str
    policy_version: int
    guild_id: str | None
    name: str
    mark: str = MARK_NONE


def _path(day: str) -> Path:
    return state_dir() / f"{day}.json"


def _parse(data: object) -> DigestThread | None:
    if not isinstance(data, Mapping):
        return None
    day, channel, surface, version = (data.get(key) for key in ("day", "channel_id", "surface", "policy_version"))
    guild, name, mark = data.get("guild_id"), data.get("name", ""), data.get("mark", MARK_NONE)
    if not (isinstance(day, str) and isinstance(channel, str) and channel.isdigit()
            and isinstance(surface, str) and type(version) is int
            and (guild is None or isinstance(guild, str)) and isinstance(name, str)
            and mark in {MARK_NONE, MARK_PENDING, MARK_DONE}):
        return None
    return DigestThread(day, channel, surface, version, guild, name, str(mark))


def load(day: str) -> DigestThread | None:
    try:
        return _parse(json.loads(_path(day).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def recent() -> list[DigestThread]:
    try:
        days = sorted(path.stem for path in state_dir().glob("*.json"))
    except OSError:
        return []
    threads = (load(day) for day in days[-KEEP_DAYS:])
    return [thread for thread in threads if thread is not None]


def find(channel_id: str) -> DigestThread | None:
    if not channel_id:
        return None
    return next((thread for thread in recent() if thread.channel_id == channel_id), None)


def _write(thread: DigestThread) -> None:
    path = _path(thread.day)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(thread), handle, ensure_ascii=False)
    os.replace(temporary, path)


class _DayLock:
    """Exclusive lock for one day's state file (class-based so a raise is never masked)."""

    def __init__(self, day: str) -> None:
        self._path = state_dir() / f"{day}.lock"
        self._handle = None

    def __enter__(self) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._handle = self._path.open("a")
        fcntl.flock(self._handle, fcntl.LOCK_EX)

    def __exit__(self, *_exc: object) -> None:
        if self._handle is not None:
            self._handle.close()


def open_thread(day: str, opener: Callable[[], DigestThread]) -> DigestThread:
    """That day's thread -- opened once by ``opener`` and then reused by every caller."""
    with _DayLock(day):
        existing = load(day)
        if existing is not None:
            return existing
        thread = opener()
        _write(thread)
        return thread


def binding(surface: object, kind: object, thread: DigestThread, directory: object, owner_id: str) -> object:
    """The thread as an approval binding of ``kind``, re-validated against live channel facts."""
    return surface.validate_stored_binding(  # type: ignore[attr-defined]
        surface.ApprovalBinding(  # type: ignore[attr-defined]
            kind, surface.ApprovalSurface(thread.surface),  # type: ignore[attr-defined]
            thread.channel_id, thread.policy_version, thread.guild_id,
        ),
        directory, owner_id,
    )


def wanted_mark(thread: DigestThread, *, pending: int, latest: bool) -> str:
    if pending > 0:
        return MARK_PENDING
    return MARK_NONE if latest else MARK_DONE


def settle(counts: Mapping[str, int], api: Callable[..., object]) -> None:
    """Bring each recent thread's mark in line with its undecided-card count.

    ``counts`` maps a thread id to its undecided cards; a thread missing from it is left
    alone (its count is unknown, so nothing is closed on a guess). Discord renames are
    made only when the mark changes, and a failed rename leaves the stored mark so the
    next tick retries.
    """
    from . import origin_notice

    threads = recent()
    latest = threads[-1].day if threads else ""
    for thread in threads:
        if thread.channel_id not in counts:
            continue
        mark = wanted_mark(thread, pending=counts[thread.channel_id], latest=thread.day == latest)
        if mark == thread.mark:
            continue
        if mark == MARK_DONE:
            changed = origin_notice.close_thread(
                api, thread.channel_id, origin_notice.ThreadOutcome.DONE, record_id=f"digest:{thread.day}")
        else:
            name = f"{PENDING_MARK}{thread.name}" if mark == MARK_PENDING else thread.name
            changed = origin_notice.rename_thread(api, thread.channel_id, name, record_id=f"digest:{thread.day}")
        if not changed:
            continue
        with _DayLock(thread.day):
            current = load(thread.day)
            if current is not None and current.channel_id == thread.channel_id:
                _write(replace(current, mark=mark))
        print(f"DIGEST-THREAD-MARK day={thread.day} mark={mark or 'none'}", file=sys.stderr)

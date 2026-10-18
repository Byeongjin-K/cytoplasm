"""Host opt-in and account-local routing; the database commits mode with migration."""
from __future__ import annotations

import fcntl
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import ParamSpec, TypeVar

from automation.ledger_policy import CARD_LINE as CARD_LINE, SURFACE as SURFACE, surface as surface
from automation.ledger import __main__ as ledger_cli
from automation.ledger.store import Store, default_path

P = ParamSpec("P")
T = TypeVar("T")


def release_switch(home: Path) -> str:
    try:
        value = (home / ".hermes/release-flags/ledger").read_bytes()
    except FileNotFoundError:
        return "off"
    if value != b"on":
        raise ValueError("LEDGER-SWITCH-REFUSED: expected the literal word on only")
    return "on"


def enabled() -> bool:
    return Store().active()


def command_prefix() -> tuple[str, ...]:
    if not enabled():
        return ("hermes", "kanban")
    return (sys.executable, "-B", str(Path(ledger_cli.__file__)))


@contextmanager
def command_lock() -> Iterator[None]:
    """Serialize caller selection/execution with the release migration and rollback."""
    path = default_path().with_suffix(".lock")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as handle:
        os.fchmod(handle.fileno(), 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def serialized(function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def locked(*args: P.args, **kwargs: P.kwargs) -> T:
        with command_lock():
            return function(*args, **kwargs)
    return locked

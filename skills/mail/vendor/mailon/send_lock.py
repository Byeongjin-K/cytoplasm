"""Serialize the one irreversible step: an actual mail send.

Every run now gets its own browser (``browser.unique_session_name``), so two concurrent
sends no longer break each other — which also removes the accident that used to stop them
from BOTH delivering. The pre-send duplicate check
(``send.DUPLICATE_SUPPRESS_WINDOW_MS``) is a check-then-act: measured 2026-09-07, two
processes cleared it 65ms apart. This lock makes that check meaningful again — the second
sender waits, then finds the first one's mail already in the mailbox and suppresses.

Dry runs are not serialized: they perform no external effect.
"""
from __future__ import annotations

import fcntl
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final, TextIO

from .send import SendSafetyError

LOCK_NAME: Final = "send.lock"
DEFAULT_TIMEOUT_S: Final = 300.0


class SendLockBusy(SendSafetyError):
    """Another send held the lock for the whole wait."""

    cause = "send_lock_busy"


def lock_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / LOCK_NAME


class hold:
    """Exclusive, cross-process hold on the send step.

    A class rather than ``@contextmanager``: a generator context manager swallows the real
    failure when an exception carrying ``__slots__`` crosses it, the same reason
    ``automation/pipeline_lock.hold`` is written this way.
    """

    __slots__ = ("_path", "_timeout_s", "_poll_s", "_sleep", "_clock", "_handle")

    def __init__(
        self,
        path: Path | str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        poll_s: float = 1.0,
        sleep: Callable[[float], object] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = Path(path)
        self._timeout_s = timeout_s
        self._poll_s = poll_s
        self._sleep = sleep
        self._clock = clock
        self._handle: TextIO | None = None

    def __enter__(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        deadline = self._clock() + self._timeout_s
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if self._clock() >= deadline:
                    handle.close()
                    raise SendLockBusy(
                        f"another send has held {self._path.name} for "
                        f"{self._timeout_s:.0f}s; refusing to send in parallel"
                    ) from None
                self._sleep(self._poll_s)
                continue
            self._handle = handle
            return None

    def __exit__(self, *_exc: object) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

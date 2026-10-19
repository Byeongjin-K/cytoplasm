"""Account environment and process lock still used by the retained peer executor."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import IO


def load_env_secrets(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        key, separator, value = raw_line.strip().partition("=")
        if separator and key and not key.startswith("#") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def acquire_single_instance_lock(lock_path: Path) -> IO[str] | None:
    """Return the peer's nonblocking flock or None when another tick holds it."""
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle

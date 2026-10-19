"""Reversible local gws replacement under one account-owned lock."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, assert_never

from automation.google_gate import token_client
from automation.google_gate.mode import Mode, marker, read_mode
from automation.google_gate.token_client import GateError, port_number


@dataclass(frozen=True, slots=True)
class Paths:
    home: Path

    @property
    def binary(self) -> Path:
        return self.home / ".local/bin/gws"

    @property
    def real(self) -> Path:
        return self.home / ".local/libexec/google-gate/gws"

    @property
    def runtime(self) -> Path:
        return self.real.with_name("token_client.py")

    def script(self, port: int) -> bytes:
        return (
            "#!/usr/bin/python3 -I\n"
            "# autophagy-google-gate shim\n"
            "import runpy, sys\n"
            f"runtime = runpy.run_path({str(self.runtime)!r})\n"
            f"raise SystemExit(runtime['execute']({port}, {str(self.real)!r}, sys.argv[1:]))\n"
        ).encode()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    """Stage in the destination filesystem, then rename once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def installed(paths: Paths) -> Mode | None:
    """Refuse partial installation or a foreign replacement before any write."""
    state = marker(paths.home)
    if (not state.exists() and not state.is_symlink()
            and not paths.real.exists() and not paths.real.is_symlink()):
        return None
    if any(p.is_symlink() for p in (state, paths.binary, paths.real, paths.runtime)):
        raise GateError("foreign-file")
    mode = read_mode(paths.home)
    if (not paths.binary.is_file() or not paths.real.is_file()
            or paths.binary.read_bytes() != paths.script(mode.port)
            or not paths.runtime.is_file()
            or paths.runtime.read_bytes() != Path(token_client.__file__).read_bytes()):
        raise GateError("foreign-file")
    if digest(paths.real) != mode.sha256:
        raise GateError("real-sha-mismatch")
    return mode


def change(action: Literal["install", "remove", "status"], paths: Paths, port: int) -> str:
    port_number(port)
    if action == "status":
        mode = installed(paths)
        if mode:
            return f"installed real-sha256={mode.sha256}"
        original = digest(paths.binary) if paths.binary.is_file() else "unavailable"
        return f"absent real-sha256={original}"
    paths.real.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = paths.real.parent.with_suffix(".lock")
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        mode = installed(paths)
        match action:
            case "install":
                if mode is not None:
                    if mode.port != port:
                        raise GateError("port-differs")
                    return f"installed real-sha256={mode.sha256}"
                source = paths.binary
                if source.is_symlink() or not source.is_file() or not os.access(source, os.X_OK):
                    raise GateError("binary-required")
                if paths.runtime.exists() or paths.runtime.is_symlink():
                    raise GateError("foreign-file")
                original = digest(source)
                mode = Mode(port, datetime.now(timezone.utc).isoformat(), original)
                atomic_write(paths.runtime, Path(token_client.__file__).read_bytes(), 0o600)
                source.replace(paths.real)
                try:
                    atomic_write(source, paths.script(port), 0o755)
                    atomic_write(marker(paths.home), json.dumps(asdict(mode)).encode() + b"\n", 0o600)
                except OSError:
                    paths.real.replace(source)
                    paths.runtime.unlink()
                    raise
                return f"installed real-sha256={original}"
            case "remove":
                if mode is None:
                    return "absent"
                paths.real.replace(paths.binary)
                marker(paths.home).unlink()
                paths.runtime.unlink()
                return f"absent real-sha256={mode.sha256}"
            case unreachable:
                assert_never(unreachable)

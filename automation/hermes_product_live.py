"""Read-only, fail-closed checks for the exact Hermes product removal paths."""
from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from automation.deploy_declarations import all_declarations
from automation.watcher_manifest import ManifestError


# The kernel hides a non-dumpable process's cwd and fds even from its own uid (seen on the node
# 2026-10-19: systemd --user, (sd-pam), gpg-agent, and the "sshd: agent@notty" side of an
# inbound session). These helpers never hold Hermes product files; any other process we
# cannot read still makes the whole check refuse.
OPAQUE_HELPERS: Final = frozenset({"systemd", "(sd-pam)", "gpg-agent", "ssh-agent", "ssh", "sshd"})


def _opaque_helper(entry: Path) -> bool:
    try:
        argv0 = (entry / "cmdline").read_bytes().split(b"\0", 1)[0].decode()
    except (OSError, UnicodeError):
        return False
    # sshd rewrites its argv0 to "sshd: <user>@<tty>"; the first word names the program.
    return Path(argv0.split(" ", 1)[0].rstrip(":")).name in OPAQUE_HELPERS


def process_paths(proc: Path = Path("/proc")) -> tuple[Path, ...]:
    """Only this uid's cwd/open files; disappearing processes/fds are harmless."""
    paths: list[Path] = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            links = [entry / "cwd", *list((entry / "fd").iterdir())]
            for link in links:
                try:
                    value = os.readlink(link).removesuffix(" (deleted)")
                except (FileNotFoundError, ProcessLookupError):
                    continue
                if value.startswith("/"):
                    paths.append(Path(value))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            # A process that exits mid-scan can deny its fd table on the way out (2026-10-19
            # v2.6.3 rehearsal); only a process that is still there is a real refusal.
            if _opaque_helper(entry) or not entry.exists():
                continue
            raise
    return tuple(paths)


def check(home: Path, targets: Sequence[Path], runtime: Path,
          proc_reader: Callable[[], tuple[Path, ...]] = process_paths) -> None:
    """Report only relative target names and reason codes, never unit/process text."""
    reasons: set[tuple[str, str]] = set()
    try:
        units = home / ".config/systemd/user"
        pending = [units] if units.exists() or units.is_symlink() else []
        visited: set[Path] = set()
        while pending:
            unit = pending.pop()
            if unit.is_dir():
                resolved = unit.resolve(strict=True)
                if resolved not in visited:
                    visited.add(resolved)
                    pending.extend(unit.iterdir())
                continue
            if not unit.is_file():
                raise OSError("unreadable unit entry")
            text = unit.read_text(encoding="utf-8")
            for target in targets:
                relative = target.relative_to(home).as_posix()
                # Covers absolute paths, %h, ~, $HOME and relative home paths.
                if relative in text:
                    reasons.add((relative, "unit"))
        for opened in proc_reader():
            for target in targets:
                if opened == target or target in opened.parents:
                    reasons.add((target.relative_to(home).as_posix(), "process"))
        declarations = all_declarations(runtime)
        if not declarations:
            raise ManifestError("missing runtime declarations")
        for row in declarations:
            if row.account != "agent":
                continue
            destination = home / row.destination
            for target in targets:
                if destination == target or target in destination.parents:
                    reasons.add((target.relative_to(home).as_posix(), "declaration"))
    except (OSError, UnicodeError, ManifestError) as error:
        raise ValueError("HERMES-PRODUCT-REFUSED: 사용 경로 판독 불가") from error
    for relative, reason in sorted(reasons):
        print(f"HERMES-PRODUCT-REFUSED: {relative} reason={reason}")
    if reasons:
        raise ValueError("HERMES-PRODUCT-REFUSED: 제품 경로 사용 중")

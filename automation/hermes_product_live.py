"""Read-only, fail-closed checks for the exact Hermes product removal paths."""
from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path

from automation.deploy_declarations import all_declarations
from automation.watcher_manifest import ManifestError


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
                except FileNotFoundError:
                    continue
                if value.startswith("/"):
                    paths.append(Path(value))
        except FileNotFoundError:
            continue
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

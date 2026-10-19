"""Declared removal of the three owner-retired agent timers and reminder data."""
from __future__ import annotations

import argparse
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Final

from automation.gateway_retirement import Runner, checked, run, safe_path, stopped

JOBS: Final = ("memory-curator-watch", "memory-relocate-watch", "reminder-poller")
ALGORITHM: Final = "retired-job-v1"
DATA: Final = ("state/reminders.db", "state/milestones.yaml")


def unit_paths(home: Path, name: str) -> tuple[Path, Path]:
    if name not in JOBS:
        raise ValueError("RETIRED-JOB-REFUSED: unknown job")
    base = f".config/systemd/user/autophagy-cron-{name}"
    return safe_path(home, base + ".timer"), safe_path(home, base + ".service")


def check(home: Path, name: str, runner: Runner = run) -> str:
    """Observe actual stopped units and exact paths; unreadable is never absent."""
    try:
        units = unit_paths(home, name)
        for path in units:
            stopped(path.name, runner)
        data = tuple(safe_path(home, path) for path in DATA) if name == "reminder-poller" else ()
        return "stale" if any(path.exists() for path in (*units, *data)) else "ok"
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown"


def install(home: Path, name: str, runner: Runner = run) -> None:
    """Never stop a live job implicitly; remove data only after unit removal read-back."""
    units = unit_paths(home, name)
    # Validate every path before unlinking any of them, including data symlinks.
    if name == "reminder-poller":
        for path in DATA:
            safe_path(home, path)
    for path in units:
        stopped(path.name, runner)
    if any(path.exists() for path in units):
        for path in units:
            path.unlink(missing_ok=True)
        checked(("systemctl", "--user", "daemon-reload"), runner)
    # This is the deployment confirmation: no unit files remain, and systemd still
    # reports both the timer and any in-flight service stopped and disabled.
    if any(path.exists() for path in units):
        raise ValueError("RETIRED-JOB-REFUSED: unit removal not confirmed")
    for path in units:
        stopped(path.name, runner)
    if name == "reminder-poller":
        remove_reminder_data(home)
    if check(home, name, runner) != "ok":
        raise ValueError("RETIRED-JOB-REFUSED: read-back failed")


def remove_reminder_data(home: Path) -> None:
    """Unlink only the two derived files through a non-symlink state directory FD."""
    try:
        directory = os.open(home / "state", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return
    try:
        names: list[str] = []
        for relative in DATA:
            name = Path(relative).name
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("RETIRED-JOB-REFUSED: reminder data is not a regular file")
            names.append(name)
        for name in names:
            os.unlink(name, dir_fd=directory)
    finally:
        os.close(directory)


def deploy(host: str, runtime: str, head: str) -> int:
    """Run only the declared agent cleanup after the active release matches this tree."""
    from automation.node_config import load_node_config

    config = load_node_config()
    pointer = subprocess.run(("ssh", host, shlex.join(("readlink", runtime))),
                             capture_output=True, text=True, check=False, timeout=30)
    if pointer.returncode or Path(pointer.stdout.strip()).name != head:
        return 4
    for name in JOBS:
        command = shlex.join(("sudo", "-n", "-u", config.agent_account, "-H", "env",
                              f"PYTHONPATH={runtime}", "python3", "-B", "-m",
                              "automation.retired_jobs", "install", "--name", name))
        result = subprocess.run(("ssh", host, command), check=False, timeout=150)
        if result.returncode:
            return 5
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "install", "deploy"))
    parser.add_argument("--name", choices=JOBS)
    parser.add_argument("--host")
    parser.add_argument("--runtime", default="/srv/autophagy-agent-current")
    parser.add_argument("--head")
    args = parser.parse_args(argv)
    try:
        if args.command == "deploy":
            if not args.host or not args.head:
                parser.error("deploy requires host and head")
            return deploy(args.host, args.runtime, args.head)
        if args.name is None:
            parser.error("name is required")
        if args.command == "check":
            print(check(Path.home(), args.name))
        else:
            install(Path.home(), args.name)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"RETIRED-JOB-REFUSED: {type(error).__name__}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

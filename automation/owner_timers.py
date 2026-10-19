"""Strict release-host list and node marker for owner-disabled systemd jobs."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

from automation.owner_timers_policy import PREFIX as PREFIX

MARKER: Final = ".hermes/owner-timers/off"
ROOT: Final = Path(__file__).resolve().parents[1]
NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]*")


class OwnerTimersError(ValueError):
    """An owner-off list or its read-back cannot be trusted."""


def payload(names: tuple[str, ...]) -> bytes:
    return "".join(f"{name}\n" for name in names).encode("ascii")


def parse(raw: bytes, root: Path = ROOT) -> tuple[str, ...]:
    """Accept exact sorted, unique, LF-terminated names of agent systemd declarations."""
    from automation.deploy_declarations import all_declarations

    try:
        names = tuple(raw.decode("ascii").splitlines())
    except UnicodeError as error:
        raise OwnerTimersError("non-ASCII list") from error
    if (not names or any(NAME.fullmatch(name) is None for name in names)
            or names != tuple(sorted(set(names))) or payload(names) != raw):
        raise OwnerTimersError("expected sorted unique job names, one LF-terminated line each")
    known = {d.destination for d in all_declarations(root)
             if d.kind == "cron" and d.account == "agent" and d.attr("runner") == "systemd"}
    from automation.retired_jobs import JOBS

    if set(names) - known - set(JOBS):
        raise OwnerTimersError("unknown agent systemd job")
    # Persisted owner-off lists can outlive the declarations they disabled. They
    # must neither block other jobs nor recreate a deleted schedule.
    return tuple(name for name in names if name not in JOBS)


def read_list(path: Path, root: Path = ROOT) -> tuple[str, ...]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ()
    return parse(raw, root)


def release_switch(home: Path, root: Path = ROOT) -> tuple[str, ...]:
    return read_list(home / ".hermes/release-flags/owner-timers-off", root)


def decode(value: str, root: Path = ROOT) -> tuple[str, ...]:
    """Slash-separated transport avoids shell whitespace and remote path filtering."""
    return () if value == "off" else parse(payload(tuple(value.split("/"))), root)


def surfaces(names: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Each job binds the entire list, so a subset cannot reuse the original approval."""
    digest = hashlib.sha256(payload(names)).hexdigest()
    return tuple((f"{PREFIX}{name}", digest) for name in names)


def released_off(home: Path, name: str) -> bool:
    return name in read_list(home / MARKER)


def check(home: Path, names: tuple[str, ...], root: Path = ROOT) -> str:
    """Read marker bytes, ownership, mode and actual disabled/inactive units; never write."""
    from automation.cron_timer import Node, check as timer_check, declared

    path = home / MARKER
    try:
        current = read_list(path, root)
        if current != names:
            return "stale"
        if names:
            stat = path.stat()
            if stat.st_uid != os.getuid() or stat.st_mode & 0o777 != 0o600:
                return "stale"
        node = Node(home)
        return "ok" if all(timer_check(node, declared(root, "agent", name), root=str(root))[0] == "ok"
                           for name in names) else "stale"
    except (OSError, OwnerTimersError):
        return "unknown"


def install(home: Path, names: tuple[str, ...], root: Path = ROOT) -> None:
    """Apply the current owner-off list; retired jobs have no schedule to restore."""
    _install(home, names, root)


def _install(home: Path, names: tuple[str, ...], root: Path) -> None:
    """Declared derived write, like legacy_services: atomic 0600 marker, then read-back."""
    from automation.cron_timer import Node, converge, declared

    path = home / MARKER
    previous = read_list(path, root)
    if names:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload(names))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        path.unlink(missing_ok=True)
    node = Node(home)
    for name in sorted(set(previous) | set(names)):
        converge(node, declared(root, "agent", name), root=str(root))
    if check(home, names, root) != "ok":
        raise OwnerTimersError("marker or disabled timers failed read-back")


def deploy(repo: Path, host: str, runtime: str, head: str, names: tuple[str, ...]) -> int:
    """The existing same-HEAD surface gate runs before any node mutation."""
    from automation.node_config import load_node_config

    value = "/".join(names) or "off"
    if names:
        approved = subprocess.run(
            (str(repo / "automation/release_approval_remote.sh"), "authorize-owner-timers-off",
             "--jobs", value, "--head", head), check=False,
        )
        if approved.returncode:
            print("OWNER-TIMERS-UNAUTHORIZED: same HEAD approval required", file=sys.stderr)
            return 4
    config = load_node_config()
    command = shlex.join(("sudo", "-n", "-u", config.agent_account, "-H", "env",
                          f"PYTHONPATH={runtime}", "python3", "-B", "-m",
                          "automation.owner_timers", "install", "--jobs", value))
    return 0 if subprocess.run(("ssh", host, command), check=False, timeout=180).returncode == 0 else 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("switch", "check", "install", "deploy"))
    parser.add_argument("--jobs", default="off")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--host")
    parser.add_argument("--runtime", default="/srv/autophagy-agent-current")
    parser.add_argument("--head")
    args = parser.parse_args(argv)
    try:
        if args.command == "switch":
            print("/".join(release_switch(Path.home(), args.repo)) or "off")
            return 0
        names = decode(args.jobs, args.repo)
        if args.command == "deploy":
            if not args.host or not args.head:
                parser.error("deploy requires --host and --head")
            return deploy(args.repo, args.host, args.runtime, args.head, names)
        if args.command == "check":
            state = check(Path.home(), names, args.repo)
            print(state)
            return 4 if state == "unknown" else 0
        install(Path.home(), names, args.repo)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"OWNER-TIMERS-REFUSED reason={type(error).__name__}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

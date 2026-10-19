"""Release-approved, reversible owner shutdown of declared agent timers."""
from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.cron_timer import Node, TimerError, unit_names
from automation.cron_timer_owner_off_policy import PREFIX
from automation.deploy_declarations import all_declarations

FLAG: Final = Path(".hermes/release-flags/cron-timers-off")
MARKER: Final = Path(".hermes/cron-timers/owner-off.mode")
_NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_MEMORY: Final = frozenset(("memory-curator-watch", "memory-relocate-watch"))


@dataclass(frozen=True, slots=True)
class ReleaseTarget:
    repo: Path
    host: str
    runtime: str
    head: str


def parse(raw: bytes) -> tuple[str, ...]:
    """Canonical nonempty ASCII names, sorted, unique, with one final LF."""
    try:
        names = tuple(raw.decode("ascii").splitlines())
    except UnicodeError as error:
        raise TimerError(4, "owner-off names must be ASCII") from error
    if (not names or any(_NAME.fullmatch(name) is None for name in names)
            or names != tuple(sorted(set(names)))
            or raw != "".join(f"{name}\n" for name in names).encode("ascii")):
        raise TimerError(4, "owner-off requires sorted unique job names, one per LF-terminated line")
    return names


def read_names(path: Path) -> tuple[str, ...]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ()
    return parse(raw)


def selected(value: str) -> tuple[str, ...]:
    """Transport uses a single shell-safe argument; off means absent."""
    return () if value == "off" else parse(value.replace(",", "\n").encode("ascii") + b"\n")


def declared_names(repo: Path) -> frozenset[str]:
    return frozenset(d.destination for d in all_declarations(repo)
                     if d.kind == "cron" and d.account == "agent"
                     and d.attr("runner") == "systemd") - _MEMORY


def validate(repo: Path, names: tuple[str, ...]) -> None:
    if set(names) - declared_names(repo):
        raise TimerError(4, "owner-off requires declared agent timers; memory uses hermes-memory-off")


def release_names(home: Path, repo: Path) -> tuple[str, ...]:
    names = read_names(home / FLAG)
    if names:
        validate(repo, names)
    return names


def released_off(home: Path, name: str) -> bool:
    return name in read_names(home / MARKER)


def surface(name: str) -> tuple[str, str]:
    parse(f"{name}\n".encode("ascii"))
    return f"{PREFIX}{name}:off", hashlib.sha256(b"off").hexdigest()


def install(node: Node, names: tuple[str, ...]) -> int:
    """Stop selected timers before publishing the marker; normal convergence restores removed jobs."""
    for name in names:
        timer = unit_names(name)[1]
        if node.unit("disable", "--now", timer)[0]:
            return 5
        if (node.unit("is-enabled", timer)[1].strip(),
                node.unit("is-active", timer)[1].strip()) != ("disabled", "inactive"):
            return 5
    marker = node.home / MARKER
    if names:
        marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = marker.with_suffix(".tmp")
        temporary.write_bytes("".join(f"{name}\n" for name in names).encode("ascii"))
        temporary.chmod(0o600)
        temporary.replace(marker)
    else:
        marker.unlink(missing_ok=True)
    return 0


def deploy(target: ReleaseTarget, names: tuple[str, ...]) -> int:
    """Authorize every stop before any write, using the node's exact release tree."""
    from automation.node_config import load_node_config

    pointer = subprocess.run(("ssh", target.host, shlex.join(("readlink", target.runtime))),
                             capture_output=True, text=True, check=False, timeout=30)
    if pointer.returncode or Path(pointer.stdout.strip()).name != target.head:
        return 4
    for name in names:
        result = subprocess.run(
            (str(target.repo / "automation/release_approval_remote.sh"), "authorize-cron-timer",
             "--name", name, "--head", target.head), check=False,
        )
        if result.returncode:
            return 4
    command = shlex.join((
        "sudo", "-n", "-u", load_node_config().agent_account, "-H", "env",
        f"PYTHONPATH={target.runtime}", "python3", "-B", "-m", "automation.cron_timer_owner_off",
        "install", "--jobs", ",".join(names) or "off",
    ))
    return subprocess.run(("ssh", target.host, command), check=False, timeout=150).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("switch", "install", "deploy"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--jobs", default="off")
    parser.add_argument("--host")
    parser.add_argument("--runtime", default="/srv/autophagy-agent-current")
    parser.add_argument("--head")
    args = parser.parse_args(argv)
    try:
        names = selected(args.jobs) if args.command == "install" else release_names(Path.home(), args.repo)
        if args.command == "switch":
            print(",".join(names) or "off")
            return 0
        if args.command == "deploy":
            if not args.host or not args.head:
                parser.error("deploy requires host and head")
            return deploy(ReleaseTarget(args.repo, args.host, args.runtime, args.head), names)
        validate(args.repo, names)
        return install(Node(Path.home()), names)
    except (OSError, UnicodeError, TimerError, subprocess.SubprocessError) as error:
        print(f"CRON-TIMER-OWNER-OFF-REFUSED: {error}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

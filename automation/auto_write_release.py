"""Release-host switch deployment; node settings have no manual preparation step."""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from automation import auto_write


def install(kinds: tuple[str, ...], home: Path) -> None:
    """Atomically install enabled kinds and remove the others on rollback."""
    if set(kinds) - auto_write.KINDS.keys():
        raise ValueError("unknown auto-write kind")
    root = auto_write.state_root(home)
    for kind in auto_write.KINDS:
        target = root / f"{kind}.mode"
        if kind not in kinds:
            target.unlink(missing_ok=True)
            continue
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(b"on")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    if any(auto_write.enabled(kind, home=home) != (kind in kinds) for kind in auto_write.KINDS):
        raise ValueError("AUTO-WRITE-READBACK-FAIL")


def deploy(repo: Path, host: str, runtime: str, head: str, kinds: tuple[str, ...]) -> int:
    """Authorize every enabled kind before any node setting is changed."""
    for kind in kinds:
        auto_write.surface(kind)
        approved = subprocess.run(
            [str(repo / "automation/release_approval_remote.sh"),
             "authorize-auto-write", "--kind", kind, "--head", head], check=False,
        )
        if approved.returncode:
            print(f"AUTO-WRITE-UNAUTHORIZED kind={kind}", file=sys.stderr)
            return 4
    from automation.node_config import load_node_config

    command = shlex.join((
        "sudo", "-n", "-u", load_node_config().agent_account, "-H", "env",
        f"PYTHONPATH={runtime}", "python3", "-B", "-m", "automation.auto_write_release",
        "install", "--kinds", ",".join(kinds) or "off",
    ))
    result = subprocess.run(["ssh", host, command], check=False, timeout=60)
    return 0 if result.returncode == 0 else 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.auto_write_release")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("switches")
    node = commands.add_parser("install")
    node.add_argument("--kinds", required=True)
    apply = commands.add_parser("apply")
    for name in ("repo", "host", "runtime", "head", "kinds"):
        apply.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "switches":
            print(",".join(auto_write.release_kinds(Path.home())) or "off")
            return 0
        kinds = () if args.kinds == "off" else tuple(args.kinds.split(","))
        if set(kinds) - auto_write.KINDS.keys():
            raise ValueError("unknown auto-write kind")
        if args.command == "install":
            install(kinds, Path.home())
            return 0
        return deploy(Path(args.repo), args.host, args.runtime, args.head, kinds)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"AUTO-WRITE-REFUSED reason={type(error).__name__}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

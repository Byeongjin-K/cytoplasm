"""Release-authorized migration: verify before the atomic account routing change."""
from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

from automation.ledger import migration, switch
from automation.ledger.store import LedgerError, Store


def install(mode: str) -> dict:
    """All updated callers share this lock, including their legacy subprocess path."""
    ledger = Store()
    with switch.command_lock():
        if mode == "off":
            if ledger.path.exists():
                with ledger.connect(write=True) as db:
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('mode','off')")
            return {"mode": "off"}
        if ledger.active():
            with ledger.connect() as db:
                return json.loads(db.execute("SELECT value FROM metadata WHERE key='migration'").fetchone()[0])
        boards, cards = migration.export_hermes()
        migration.verify_source_counts(boards, cards)
        keys = migration.source_keys(boards)
        # Re-read before committing: a source modified outside the three adapters must not be silently lost.
        again = migration.export_hermes()
        migration.verify_source_counts(*again)
        if (boards, cards) != again or keys != migration.source_keys(again[0]):
            raise migration.MigrationError("LEDGER-MIGRATION-REFUSED: source changed; callers unchanged")
        return migration.import_snapshot(ledger, boards, cards, keys)


def deploy(repo: Path, host: str, runtime: str, head: str, mode: str) -> int:
    """Do not touch the node until this exact HEAD's release authorizes the surface."""
    if mode == "on":
        approved = subprocess.run(
            [str(repo / "automation/release_approval_remote.sh"), "authorize-ledger", "--head", head], check=False,
        )
        if approved.returncode:
            print("LEDGER-SWITCH-UNAUTHORIZED", file=sys.stderr)
            return 4
    from automation.node_config import load_node_config

    command = shlex.join((
        "sudo", "-n", "-u", load_node_config().agent_account, "-H", "env",
        f"PYTHONPATH={runtime}", "python3", "-B", "-m", "automation.ledger.release", "install", "--mode", mode,
    ))
    completed = subprocess.run(["ssh", host, command], check=False, timeout=1800)
    return 0 if completed.returncode == 0 else 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.ledger.release")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("switch")
    check = commands.add_parser("check")
    check.add_argument("--mode", choices=("on", "off"), required=True)
    node = commands.add_parser("install")
    node.add_argument("--mode", choices=("on", "off"), required=True)
    apply = commands.add_parser("apply")
    for name in ("repo", "host", "runtime", "head"):
        apply.add_argument(f"--{name}", required=True)
    apply.add_argument("--mode", choices=("on", "off"), required=True)
    args = parser.parse_args(argv)
    try:
        match args.command:
            case "switch":
                print(switch.release_switch(Path.home()))
                return 0
            case "check":
                return 0 if switch.enabled() == (args.mode == "on") else 1
            case "install":
                print(json.dumps(install(args.mode), sort_keys=True))
                return 0
            case "apply":
                return deploy(Path(args.repo), args.host, args.runtime, args.head, args.mode)
            case _:
                return 2
    except migration.MigrationError as error:
        print(str(error), file=sys.stderr)
        return 4
    except (OSError, ValueError, KeyError, sqlite3.Error, LedgerError,
            subprocess.SubprocessError) as error:
        print(f"LEDGER-MIGRATION-REFUSED: {type(error).__name__}; callers unchanged", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

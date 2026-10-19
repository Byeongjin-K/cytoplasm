"""Declared removal of retired timers, derived data and stopped Hermes hooks."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

from automation.gateway_retirement import Runner, checked, run, safe_path, stopped

if TYPE_CHECKING:
    from automation.node_config import NodeConfig

JOBS: Final = ("memory-curator-watch", "memory-relocate-watch", "reminder-poller", "research-trends", "wiki-confirm-watch")
ALGORITHM: Final = "retired-job-v1"
DATA: Final = ("state/reminders.db", "state/milestones.yaml")
WIKI_SCRIPT: Final = ".hermes/scripts/wiki_confirm_reaction_watch.py"
SCRIPTS: Final = {
    "memory-curator-watch": ".hermes/scripts/memory_curator_watch.py",
    "memory-relocate-watch": ".hermes/scripts/memory_relocate_watch.py",
    "reminder-poller": ".hermes/scripts/poll_reminders.py",
    "wiki-confirm-watch": WIKI_SCRIPT,
}
INTEROP_FILES: Final = (
    ".hermes/interop/external-effect-tools.yaml",
    ".config/systemd/user/hermes-gateway.service.d/20-interop-guard.conf",
    ".hermes/interop/production_guard.sh",
    ".hermes/interop/pin_import_root.sh",
    ".hermes/interop/gate_driver.py",
    ".hermes/interop_runtime/automation/interop/external_effect_gate.py",
    ".hermes/interop_runtime/automation/interop/external_effect_gate_e2e.py",
    ".hermes/interop_runtime/automation/interop/hook_boundary_e2e.py",
    ".hermes/interop_runtime/automation/interop/policy_paths.py",
    ".hermes/interop_runtime/automation/interop/gateway_preflight.py",
)
INTEROP_TREES: Final = (
    ".hermes/plugins/interop-protocol",
    ".hermes/interop_runtime/automation/interop/hermes_plugin",
    ".hermes/interop_runtime/automation/interop/plugin_shim",
)


def interop_paths(home: Path) -> tuple[Path, ...]:
    """Validate the exact retired files and trees before observing or deleting anything."""
    paths = tuple(safe_path(home, relative) for relative in (*INTEROP_FILES, *INTEROP_TREES))
    for path in paths:
        if path.exists() and ((str(path.relative_to(home)) in INTEROP_FILES and not path.is_file())
                              or (str(path.relative_to(home)) in INTEROP_TREES and not path.is_dir())):
            raise ValueError("RETIRED-JOB-REFUSED: interop path type mismatch")
        if path.is_dir():
            for child in path.rglob("*"):
                safe_path(home, str(child.relative_to(home)))
    return paths


def unit_paths(home: Path, name: str) -> tuple[Path, Path]:
    if name not in JOBS:
        raise ValueError("RETIRED-JOB-REFUSED: unknown job")
    base = f".config/systemd/user/autophagy-cron-{name}"
    return safe_path(home, base + ".timer"), safe_path(home, base + ".service")


_RUNNING: Final = {"is-active": ("active", "activating", "reloading", "deactivating"),
                   "is-enabled": ("enabled", "enabled-runtime", "linked", "linked-runtime", "alias")}


def running(unit: str, runner: Runner) -> bool:
    """True when systemd plainly reports the unit running or enabled; False when stopped.

    A running timer is work for the deployer (`stale`), not an unreadable state: reporting it as
    `unknown` made the release hold the cleanup without ever calling it.
    """
    try:
        stopped(unit, runner)
        return False
    except ValueError:
        pass
    for verb, states in _RUNNING.items():
        result = runner(("systemctl", "--user", verb, unit))
        if result.returncode in (0, 1, 3, 4) and result.stdout.strip() in states:
            return True
    raise ValueError("RETIRED-JOB-REFUSED: unit state unreadable")


def check(home: Path, name: str, runner: Runner = run) -> str:
    """Observe actual units and exact paths; unreadable is never absent."""
    try:
        if name == "interop-plugin":
            return "stale" if any(path.exists() for path in interop_paths(home)) else "ok"
        units = unit_paths(home, name)
        scripts = (safe_path(home, SCRIPTS[name]),) if name in SCRIPTS else ()
        live = [running(path.name, runner) for path in units]
        data = tuple(safe_path(home, path) for path in DATA) if name == "reminder-poller" else ()
        return "stale" if any(live) or any(path.exists() for path in (*units, *scripts, *data)) else "ok"
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown"


def quiesce(unit: str, runner: Runner) -> None:
    """Stop and disable a job the owner retired (2026-10-19 "끄지 말고 삭제", Q282).

    The release approval authorizes this; a timer that still runs (the node's reminder poller)
    would otherwise refuse every release. Nothing is deleted unless the read-back says stopped.
    """
    try:
        stopped(unit, runner)
        return
    except ValueError:
        pass
    verb = ("disable", "--now") if unit.endswith(".timer") else ("stop",)
    checked(("systemctl", "--user", *verb, unit), runner)
    stopped(unit, runner)


def install(home: Path, name: str, runner: Runner = run) -> None:
    """Stop the retired job, then remove data only after unit removal read-back."""
    if name == "interop-plugin":
        paths = interop_paths(home)
        if not any(path.exists() for path in paths):
            return
        stopped("hermes-gateway.service", runner)
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        checked(("systemctl", "--user", "daemon-reload"), runner)
        stopped("hermes-gateway.service", runner)
        if check(home, name, runner) != "ok":
            raise ValueError("RETIRED-JOB-REFUSED: interop removal not confirmed")
        return
    units = unit_paths(home, name)
    # research-trends keeps its script: topics imports and retained data still use it.
    scripts = (safe_path(home, SCRIPTS[name]),) if name in SCRIPTS else ()
    # Validate every path before unlinking any of them, including data symlinks.
    if name == "reminder-poller":
        for path in DATA:
            safe_path(home, path)
    for path in units:
        quiesce(path.name, runner)
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
    for script in scripts:
        script.unlink(missing_ok=True)
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
    accounts = [config.agent_account]
    if not _peer_removed(host, config):
        accounts.append(config.peer_account)
    for account in accounts:
        command = shlex.join(("sudo", "-n", "-u", account, "-H", "env",
                              f"PYTHONPATH={runtime}", "python3", "-B", "-m",
                              "automation.retired_jobs", "install", "--name", "interop-plugin"))
        if subprocess.run(("ssh", host, command), check=False, timeout=150).returncode:
            return 5
    return 0


def _peer_removed(host: str, config: NodeConfig) -> bool:
    """P5 retired peer and the owner's root block then deleted the account: no plugin is left.

    Same probe as deploy_all.sh retire_peer; the marker is ops 0600, so only ops reads it.
    An absent account without the marker still runs the cleanup and fails closed.
    """
    marker = shlex.quote(str(config.private_root / "peer-retired"))
    probe = (f"sudo -n -u {shlex.quote(config.ops_account)} -H bash -c "
             + shlex.quote(f"cmp -s {marker} <(printf retired)")
             + f" && ! getent passwd {shlex.quote(config.peer_account)} >/dev/null")
    return subprocess.run(("ssh", host, probe), check=False, timeout=30).returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "install", "deploy"))
    parser.add_argument("--name", choices=(*JOBS, "interop-plugin"))
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

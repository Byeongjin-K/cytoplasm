"""Reversible, release-bound shutdown of the old reporting and board views."""
from __future__ import annotations

import argparse
import fcntl
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

UNITS: Final = {
    "report-hub": ("report-hub-collector.service", "report-hub-dashboard.service"),
    "hermes-dashboard": ("hermes-dashboard.service",),
    "hermes-memory": (
        "autophagy-cron-memory-curator-watch.timer",
        "autophagy-cron-memory-relocate-watch.timer",
    ),
}
ALGORITHM: Final = "legacy-service-v1"
Run = Callable[[tuple[str, ...]], tuple[int, str]]


def memory_timer_off(home: Path, name: str) -> bool:
    """Only the deployed release marker changes the two memory timers' expected state."""
    return name in ("memory-curator-watch", "memory-relocate-watch") and released_off(home, "hermes-memory")


def release_mode(home: Path, name: str) -> str:
    """Only the exact host file bytes opt into shutdown."""
    try:
        value = (home / f".hermes/release-flags/{name}-off").read_bytes()
    except FileNotFoundError:
        return "on"
    if value != b"off":
        raise ValueError("LEGACY-SERVICE-REFUSED: expected literal off")
    return "off"


def marker_path(home: Path, name: str) -> Path:
    return home / f".hermes/legacy-services/{name}.mode"


def released_off(home: Path, name: str) -> bool:
    try:
        return marker_path(home, name).read_bytes() == b"off"
    except OSError:
        return False


def _run(argv: tuple[str, ...]) -> tuple[int, str]:
    result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=60,
                            env={**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"})
    return result.returncode, result.stdout.strip()


def check(home: Path, name: str, mode: str, run: Run = _run) -> str:
    """Unmanaged legacy installs remain unchanged; marked shutdowns are read back."""
    path = marker_path(home, name)
    try:
        marked = path.read_bytes()
    except FileNotFoundError:
        return "ok" if mode == "on" else "stale"
    except OSError:
        return "unknown"
    if marked != b"off" or mode != "off":
        return "stale"
    for unit in UNITS[name]:
        if name == "hermes-memory":
            code, output = run(("systemctl", "--user", "is-enabled", unit))
            if output.strip() != "disabled":
                return "unknown" if code not in (0, 1, 3, 4) else "stale"
        code, output = run(("systemctl", "--user", "is-active", unit))
        if output.strip() != "inactive":
            return "unknown" if code not in (0, 3, 4) else "stale"
    return "ok"


def install(home: Path, name: str, mode: str, run: Run = _run) -> int:
    """Refuse unresolved choices before stopping either memory timer, under its watcher lock."""
    if name == "hermes-memory" and mode == "off":
        from automation.memory_relocate.model import RelocationError
        from automation.memory_relocate.relocation_store import RelocationStore

        lock = home / ".hermes/memory-relocate/watch.lock"
        lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock.open("a", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("MEMORY-OFF-REFUSED: 메모리 이동 실행 중, 타이머 무변경") from error
            try:
                pending = RelocationStore(home / ".hermes/memory-curator/relocations.json").pending()
            except RelocationError as error:
                raise ValueError("MEMORY-OFF-REFUSED: 메모리 선택 상태 판독 불가, 타이머 무변경") from error
            if pending:
                raise ValueError(
                    f"MEMORY-OFF-REFUSED: 미결 메모리 선택 {len(pending)}건을 먼저 종결하세요. 타이머 무변경"
                )
            return _install(home, name, mode, run)
    return _install(home, name, mode, run)


def _install(home: Path, name: str, mode: str, run: Run) -> int:
    """Preserve all files; memory uses disable/enable on timers, views use stop/start."""
    marker = marker_path(home, name)
    if mode == "on" and not marker.exists():
        return 0
    for unit in UNITS[name]:
        command = ("disable" if mode == "off" else "enable", "--now") if name == "hermes-memory" else (
            "stop" if mode == "off" else "start",
        )
        code, _ = run(("systemctl", "--user", *command, unit))
        if code:
            loaded = run(("systemctl", "--user", "show", unit, "--property=LoadState", "--value"))[1]
            if mode == "off" and loaded.strip() == "not-found":
                continue  # Never installed on this node, so nothing to stop (PR #104 gap 7).
            print(f"LEGACY-SERVICE-FAIL unit={unit} step={command[0]} rc={code}", file=sys.stderr)
            return 5
        if name == "hermes-memory":
            code, output = run(("systemctl", "--user", "is-enabled", unit))
            if output.strip() != ("disabled" if mode == "off" else "enabled") or code not in (0, 1, 3):
                print(f"LEGACY-SERVICE-FAIL unit={unit} step=is-enabled state={output.strip()}", file=sys.stderr)
                return 5
        code, output = run(("systemctl", "--user", "is-active", unit))
        if output.strip() != ("inactive" if mode == "off" else "active") or code not in (0, 3):
            print(f"LEGACY-SERVICE-FAIL unit={unit} step=is-active state={output.strip()}", file=sys.stderr)
            return 5
    if mode == "off":
        marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = marker.with_suffix(".tmp")
        temporary.write_bytes(b"off")
        temporary.chmod(0o600)
        temporary.replace(marker)
    else:
        marker.unlink()
    return 0


def deploy(repo: Path, host: str, runtime: str, head: str) -> int:
    """The existing release surface gate authorizes every host-selected stop."""
    from automation.node_config import load_node_config

    config = load_node_config()
    modes = {name: release_mode(Path.home(), name) for name in UNITS}
    pointer = subprocess.run(("ssh", host, shlex.join(("readlink", runtime))),
                             capture_output=True, text=True, check=False, timeout=30)
    if pointer.returncode or Path(pointer.stdout.strip()).name != head:
        return 4
    for name, mode in modes.items():
        if mode == "off":
            result = subprocess.run(
                (str(repo / "automation/release_approval_remote.sh"), "authorize-legacy-service",
                 "--name", name, "--head", head), check=False,
            )
            if result.returncode:
                return 4
    for name in sorted(modes, key=lambda item: item != "hermes-memory"):
        mode = modes[name]
        account = config.ops_account if name == "report-hub" else config.agent_account
        command = shlex.join(("sudo", "-n", "-u", account, "-H", "env", f"PYTHONPATH={runtime}",
                              "python3", "-B", "-m", "automation.legacy_services",
                              "install", "--name", name, "--mode", mode))
        result = subprocess.run(("ssh", host, command), check=False, timeout=150)
        if result.returncode:
            return 5
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("switch", "install", "check", "deploy"))
    parser.add_argument("--name", choices=tuple(UNITS))
    parser.add_argument("--mode", choices=("on", "off"), default="on")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--runtime", default="/srv/autophagy-agent-current")
    parser.add_argument("--head")
    args = parser.parse_args(argv)
    try:
        if args.command == "deploy":
            if args.repo is None or not args.host or not args.head:
                parser.error("deploy requires repo, host and head")
            return deploy(args.repo, args.host, args.runtime, args.head)
        if args.name is None:
            parser.error("name is required")
        if args.command == "switch":
            print(release_mode(Path.home(), args.name))
            return 0
        if args.command == "check":
            print(check(Path.home(), args.name, args.mode))
            return 0
        return install(Path.home(), args.name, args.mode)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        message = str(error) if str(error).startswith("MEMORY-OFF-REFUSED:") else type(error).__name__
        print(f"LEGACY-SERVICE-REFUSED reason={message}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

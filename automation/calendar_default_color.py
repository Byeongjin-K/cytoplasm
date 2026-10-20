"""Release-bound default color for newly frozen calendar insert commands."""
from __future__ import annotations

import argparse
import hashlib
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Final

COLORS: Final = tuple(str(value) for value in range(1, 12))
PREFIX: Final = "calendar-default-color:"
MARKER: Final = ".hermes/calendar-default-color.mode"
ALGORITHM: Final = "calendar-default-color-v1"


def color_label(color: str) -> str:
    """Use Sage's familiar Korean name; other Google ids remain explicit."""
    return "세이지" if color == "2" else f"Google 색상 {color}"


def surface(color: str) -> tuple[str, str]:
    if color not in COLORS:
        raise ValueError("CALENDAR-COLOR-REFUSED: expected Google colorId 1-11")
    return PREFIX + color, hashlib.sha256(color.encode("ascii")).hexdigest()


def release_mode(home: Path) -> str:
    """Only exact host bytes opt in; invalid or unreadable files refuse."""
    try:
        value = (home / ".hermes/release-flags/calendar-default-color").read_bytes()
    except FileNotFoundError:
        return "off"
    if value not in tuple(color.encode("ascii") for color in COLORS):
        raise ValueError("CALENDAR-COLOR-REFUSED: expected literal colorId 1-11")
    return value.decode("ascii")


def node_color(home: Path) -> str | None:
    """Read the released marker from HERMES_HOME only while building a draft."""
    try:
        value = (home / "calendar-default-color.mode").read_bytes()
    except FileNotFoundError:
        return None
    if value == b"off":
        return None
    if value not in tuple(color.encode("ascii") for color in COLORS):
        raise ValueError("CALENDAR-COLOR-REFUSED: invalid node marker")
    return value.decode("ascii")


def prepare_deploy(repo: Path, host: str, source: Path) -> tuple[str, str]:
    """Authorize the exact HEAD and color before preparing declared marker bytes."""
    from automation.node_config import NodeConfigError, load_node_config

    mode = release_mode(Path.home())
    try:
        config = load_node_config()
    except NodeConfigError as error:
        raise ValueError("노드 설정을 읽을 수 없습니다") from error
    host = host or config.deploy_ssh_host
    if not host or host.startswith("-"):
        raise ValueError("배포 SSH 별칭이 필요합니다")
    head = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    pointer = subprocess.run(
        ("ssh", host, shlex.join(("readlink", str(config.release_current)))),
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    if Path(pointer).name != head:
        raise ValueError("노드와 배포 HEAD가 다릅니다")
    if mode != "off":
        approved = subprocess.run(
            (str(repo / "automation/release_approval_remote.sh"),
             "authorize-calendar-default-color", "--head", head, "--color", mode),
            capture_output=True, text=True, check=False, timeout=120,
        )
        if approved.returncode:
            raise ValueError("같은 HEAD와 색상값의 릴리스 승인이 필요합니다")
    source.write_bytes(mode.encode("ascii"))
    return host, config.agent_account


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("switch", "prepare"))
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--host", default="")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "switch":
            print(release_mode(Path.home()))
        else:
            if args.repo is None or args.source is None:
                parser.error("prepare requires repo and source")
            host, account = prepare_deploy(args.repo, args.host, args.source)
            print(f"{host}\n{account}")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("CALENDAR-COLOR-REFUSED: 스위치·노드 HEAD·릴리스 승인을 확인하세요", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

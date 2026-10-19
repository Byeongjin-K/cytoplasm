"""Release-bound opt-in for institutional HTML replies; absence keeps plain text."""
from __future__ import annotations

import argparse
import hashlib
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Final

SURFACE: Final = "mail-html-reply:on"
CARD_LINE: Final = "기관메일 회신 새 서식(원문 서식 인용·맑은 고딕) 켜기"
MARKER: Final = ".hermes/mail-html-reply.mode"
ALGORITHM: Final = "mail-html-reply-v1"


def surface() -> tuple[str, str]:
    return SURFACE, hashlib.sha256(b"on").hexdigest()


def release_mode(home: Path) -> str:
    """Only exact host bytes opt in; unreadable or malformed files refuse."""
    try:
        value = (home / ".hermes/release-flags/mail-html-reply").read_bytes()
    except FileNotFoundError:
        return "off"
    if value != b"on":
        raise ValueError("MAIL-HTML-REPLY-REFUSED: expected literal on")
    return "on"


def enabled(home: Path) -> bool:
    """The node marker is read only when building the frozen draft, never at send time."""
    try:
        value = (home / MARKER).read_bytes()
    except FileNotFoundError:
        return False
    if value not in (b"on", b"off"):
        raise ValueError("MAIL-HTML-REPLY-REFUSED: invalid node marker")
    return value == b"on"


def prepare_deploy(repo: Path, host: str, source: Path) -> tuple[str, str]:
    """Verify the active HEAD and existing release authority before preparing marker bytes."""
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
    if mode == "on":
        approved = subprocess.run(
            (str(repo / "automation/release_approval_remote.sh"),
             "authorize-mail-html-reply", "--head", head),
            capture_output=True, text=True, check=False, timeout=120,
        )
        if approved.returncode:
            raise ValueError("같은 HEAD의 릴리스 승인이 필요합니다")
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
        print("MAIL-HTML-REPLY-REFUSED: 스위치·노드 HEAD·릴리스 승인을 확인하세요", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

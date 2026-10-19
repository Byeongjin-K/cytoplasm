"""Release-bound opt-in for institutional HTML replies; absence keeps plain text."""
from __future__ import annotations

import argparse
import hashlib
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("switch",))
    parser.parse_args(argv)
    try:
        print(release_mode(Path.home()))
        return 0
    except (OSError, ValueError):
        print("MAIL-HTML-REPLY-REFUSED: 릴리스 스위치는 정확한 on만 허용합니다", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

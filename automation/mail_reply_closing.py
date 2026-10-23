"""Release-bound opt-out for the reply-draft closing; absence keeps today's closing."""
from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Final

SURFACE: Final = "mail-reply-closing:omit"
CARD_LINE: Final = "메일 회신 초안 맺음말(감사합니다·이름 올림) 빼기 — 계정 서명이 맺음을 붙임"
MARKER: Final = ".hermes/mail-reply-closing.mode"
ALGORITHM: Final = "mail-reply-closing-v1"

#: Each shipped reply prompt's closing rule and the rule that replaces it under omit.
#: Only these exact lines change, so keep renders the template byte for byte.
CLOSING_RULES: Final = (
    (
        '- **맺음**: 상대의 다음 액션을 청하는 문장 1개 → "감사합니다." → "<owner-name> 올림" 순으로 고정한다.'
        ' (예: "검토하신 후 의견 주시면 감사하겠습니다.")',
        "- **맺음**: 상대의 다음 액션을 청하는 문장 1개로 본문을 끝낸다."
        ' (예: "검토하신 후 의견 주시면 감사하겠습니다.") 그 뒤에 "감사합니다."·"<owner-name> 올림"·이름·서명을'
        " 쓰지 않는다 — 발송 계정의 기본 서명이 맺음말과 이름을 붙인다.",
    ),
    (
        '- 마지막 줄은 "감사합니다."로 끝낸다. 이름/직함/전화번호 등 서명 정보를 지어내지 않는다(발송 계정의 기본 서명이 붙는다).',
        '- 마지막 줄은 상대의 다음 액션을 청하는 문장으로 끝내고, 그 뒤에 "감사합니다."·이름 올림 같은 맺음말을 쓰지 않는다.'
        " 이름/직함/전화번호 등 서명 정보를 지어내지 않는다(발송 계정의 기본 서명이 맺음말과 이름을 붙인다).",
    ),
)
#: A trailing line that is only a sign-off: "감사합니다." or "[소속 ]<2~4자 한글 이름> 올림|드림".
_SIGN_OFF: Final = re.compile(r"^(?:감사합니다[.!]?|(?:\S{1,20}\s)?[가-힣]{2,4}\s?(?:올림|드림)[.]?)$")


def surface() -> tuple[str, str]:
    return SURFACE, hashlib.sha256(b"omit").hexdigest()


def release_mode(home: Path) -> str:
    """Only exact host bytes opt out; unreadable or malformed files refuse."""
    try:
        value = (home / ".hermes/release-flags/mail-reply-closing").read_bytes()
    except FileNotFoundError:
        return "keep"
    if value != b"omit":
        raise ValueError("MAIL-REPLY-CLOSING-REFUSED: expected literal omit")
    return "omit"


def node_mode(home: Path) -> str:
    """The node marker is read only while building a draft; absence is today's closing."""
    try:
        value = (home / MARKER).read_bytes()
    except FileNotFoundError:
        return "keep"
    if value not in (b"keep", b"omit"):
        raise ValueError("MAIL-REPLY-CLOSING-REFUSED: invalid node marker")
    return value.decode("ascii")


def apply_to_template(template: str, mode: str) -> str:
    """Swap the closing rule under omit; a template without a known rule fails closed."""
    if mode == "keep":
        return template
    if mode != "omit":
        raise ValueError("MAIL-REPLY-CLOSING-REFUSED: unknown mode")
    for current, replacement in CLOSING_RULES:
        if current in template:
            return template.replace(current, replacement)
    raise ValueError("MAIL-REPLY-CLOSING-REFUSED: reply prompt has no known closing rule")


def strip_sign_off(body: str, mode: str) -> str:
    """Under omit, drop trailing sign-off lines the model still wrote; keep is untouched."""
    if mode == "keep":
        return body
    lines = body.rstrip().splitlines()
    while len(lines) > 1 and _SIGN_OFF.match(lines[-1].strip()):
        lines.pop()
        while len(lines) > 1 and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines)


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
    if mode == "omit":
        approved = subprocess.run(
            (str(repo / "automation/release_approval_remote.sh"),
             "authorize-mail-reply-closing", "--head", head),
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
        print("MAIL-REPLY-CLOSING-REFUSED: 스위치·노드 HEAD·릴리스 승인을 확인하세요", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

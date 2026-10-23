"""Release-bound opt-in: a mail that names a day but no time becomes an all-day draft.

Owner decision 2026-10-23 (Q334): "응, 종일 일정 초안으로 (카드가 조금 늘어남)".
Absent or ``off`` keeps today's behaviour byte for byte: the classify prompt asks for a
schedule line only when the mail names both a date and a time, and the calendar re-asks
(exit 5) for a line without a time. Under ``on`` the classify prompt also asks for a
date-only line (evaluation days, submission deadlines) and the mail transport asks the
calendar to draft such a line as an all-day event.
"""
from __future__ import annotations

import argparse
import hashlib
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Final

SURFACE: Final = "mail-schedule-all-day:on"
CARD_LINE: Final = "메일 다이제스트: 날짜만 있고 시각이 없는 일정(평가일·제출 마감)도 종일 일정 초안 카드로"
MARKER: Final = ".hermes/mail-schedule-all-day.mode"
ALGORITHM: Final = "mail-schedule-all-day-v1"
#: The env pair the mail transport sets for the calendar child. An env pair, not a flag:
#: a calendar CLI older than this ignores it and keeps today's exit-5 re-ask.
CALENDAR_ENV: Final = "CALENDAR_ALL_DAY_UNDATED"

#: The shipped classify prompt's two schedule rules and their replacements under ``on``.
#: Only these exact lines change, so ``off`` renders the template byte for byte.
PROMPT_RULES: Final = (
    (
        "- schedule_needed: 회의/미팅/세미나 등 일정 등록이 필요한 메일이면 true.",
        "- schedule_needed: 회의/미팅/세미나 등 일정 등록이 필요한 메일이면 true."
        " 평가일·발표일·제출 마감처럼 날짜가 정해진 일정도 true.",
    ),
    (
        '- schedule_text: schedule_needed가 true일 때, 일정을 한 문장의 한국어로 요약 (예: "7월 20일 오후 3시 연구 미팅").'
        ' 날짜와 시각이 본문에 명시된 경우에만 그대로 옮겨 적고, 불명확하면 빈 문자열 "".',
        '- schedule_text: schedule_needed가 true일 때, 일정을 한 문장의 한국어로 요약 (예: "7월 20일 오후 3시 연구 미팅").'
        " 날짜와 시각이 본문에 명시되면 그대로 옮겨 적는다. 날짜만 명시되고 시각이 없으면 시각을 지어내지 말고"
        ' 날짜와 일정 이름만 적는다 (예: "11월 18일 연차평가", 여러 날이면 "12월 22일~23일 겨울학교").'
        ' 날짜가 불명확하면 빈 문자열 "".',
    ),
)


def surface() -> tuple[str, str]:
    return SURFACE, hashlib.sha256(b"on").hexdigest()


def release_mode(home: Path) -> str:
    """Only exact host bytes opt in; unreadable or malformed files refuse."""
    try:
        value = (home / ".hermes/release-flags/mail-schedule-all-day").read_bytes()
    except FileNotFoundError:
        return "off"
    if value != b"on":
        raise ValueError("MAIL-SCHEDULE-ALL-DAY-REFUSED: expected literal on")
    return "on"


def node_mode(hermes_home: Path) -> str:
    """The node marker under HERMES_HOME; absence is today's behaviour."""
    try:
        value = (hermes_home / "mail-schedule-all-day.mode").read_bytes()
    except FileNotFoundError:
        return "off"
    if value not in (b"on", b"off"):
        raise ValueError("MAIL-SCHEDULE-ALL-DAY-REFUSED: invalid node marker")
    return value.decode("ascii")


def apply_to_template(template: str, mode: str) -> str:
    """Swap both schedule rules under ``on``; a prompt missing either fails closed."""
    if mode == "off":
        return template
    if mode != "on":
        raise ValueError("MAIL-SCHEDULE-ALL-DAY-REFUSED: unknown mode")
    for current, replacement in PROMPT_RULES:
        if template.count(current) != 1:
            raise ValueError("MAIL-SCHEDULE-ALL-DAY-REFUSED: classify prompt has no known schedule rule")
        template = template.replace(current, replacement)
    return template


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
             "authorize-mail-schedule-all-day", "--head", head),
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
        print("MAIL-SCHEDULE-ALL-DAY-REFUSED: 스위치·노드 HEAD·릴리스 승인을 확인하세요", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

"""Read the released all-day schedule marker for the classify prompt and the calendar child."""
from __future__ import annotations

import sys
from pathlib import Path

import mail_runtime

#: Same name as ``automation.mail_schedule_all_day.CALENDAR_ENV`` (pinned by a unit test).
CALENDAR_ENV = "CALENDAR_ALL_DAY_UNDATED"


def mode() -> str:
    """``on`` only for the exact released marker; a broken marker keeps today's behaviour."""
    root = str(mail_runtime.repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from automation.mail_schedule_all_day import node_mode
    except ImportError:
        # A code tree older than this mail skill has no switch: today's behaviour.
        print("MAIL-SCHEDULE-ALL-DAY-UNAVAILABLE: 종일 일정 초안 모듈이 없어 끕니다", file=sys.stderr)
        return "off"
    try:
        return node_mode(Path.home() / ".hermes")
    except (OSError, ValueError):
        print("MAIL-SCHEDULE-ALL-DAY-MARKER-INVALID: 표식을 읽을 수 없어 종일 일정 초안을 끕니다", file=sys.stderr)
        return "off"


def classify_template(template: str) -> str:
    selected = mode()
    if selected == "off":
        return template
    from automation.mail_schedule_all_day import apply_to_template

    return apply_to_template(template, selected)

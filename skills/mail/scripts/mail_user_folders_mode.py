"""Read the released user-folder marker for the periodic collection and the triage list (issue #251)."""
from __future__ import annotations

import sys
from pathlib import Path

import mail_runtime

#: Days back a user-folder collection reaches when the caller names none.
DEFAULT_SINCE_DAYS = 7
#: Most days back a user-folder collection may reach (same cap as the YooDori gate).
MAX_SINCE_DAYS = 31


def mode() -> str:
    """``on`` only for the exact released marker; a broken marker keeps today's behaviour."""
    root = str(mail_runtime.repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from automation.mail_user_folders import node_mode
    except ImportError:
        # A code tree older than this mail skill has no switch: today's behaviour.
        print("MAIL-USER-FOLDERS-UNAVAILABLE: 사용자 폴더 수집 모듈이 없어 끕니다", file=sys.stderr)
        return "off"
    try:
        return node_mode(Path.home() / ".hermes")
    except (OSError, ValueError):
        print("MAIL-USER-FOLDERS-MARKER-INVALID: 표식을 읽을 수 없어 사용자 폴더 수집을 끕니다", file=sys.stderr)
        return "off"

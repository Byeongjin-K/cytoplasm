"""Choose a reply format before approval; the test exception is owner-mailbox only."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

import mail_runtime
import triage_recipient

TEST_CARD_LINE: Final = "새 서식 시험 회신(형 주소에만)"


def require_owner_recipients(to: str, cc: str, bcc: tuple[str, ...]) -> None:
    """Parse every mailbox, including Cc and Bcc, using the existing strict input boundary."""
    import mail_bcc
    from triage_gate import GateError

    owner = triage_recipient.owner_address().strip().casefold()
    reason = "새 서식 시험은 형 기관메일 주소만 To·Cc·Bcc에 넣을 수 있습니다"
    try:
        own = mail_bcc._mailboxes(owner, 1)
        recipients = tuple(address.casefold() for value in (to, cc, *bcc)
                           if value.strip() for address in mail_bcc._mailboxes(value, 1))
    except GateError:
        raise GateError(reason, 2) from None
    if len(own) != 1 or not to.strip() or not recipients or any(address != own[0].casefold() for address in recipients):
        raise GateError(reason, 2)


def select(format_test: str, to: str, cc: str, bcc: tuple[str, ...]) -> str:
    """Freeze html-test, html or the legacy plain format at draft creation."""
    from triage_gate import GateError

    if format_test:
        if format_test != "html":
            raise GateError("새 서식 시험은 --format-test html만 허용합니다", 2)
        require_owner_recipients(to, cc, bcc)
        return "html-test"
    root = str(mail_runtime.repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from automation.mail_html_reply import enabled

    try:
        return "html" if enabled(Path.home()) else ""
    except (OSError, ValueError):
        raise GateError("기관메일 회신 서식 표식을 읽을 수 없습니다 — 초안 생성 거부", 3) from None


def closing_template(template: str) -> tuple[str, str]:
    """Apply the released closing marker to a reply prompt; return (mode, template)."""
    from triage_gate import GateError

    root = str(mail_runtime.repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from automation.mail_reply_closing import apply_to_template, node_mode

    try:
        mode = node_mode(Path.home())
        return mode, apply_to_template(template, mode)
    except (OSError, ValueError):
        raise GateError("회신 맺음말 표식 또는 프롬프트를 확인할 수 없습니다 — 초안 생성 거부", 3) from None


def closing_body(body: str, mode: str) -> str:
    from automation.mail_reply_closing import strip_sign_off

    return strip_sign_off(body, mode)

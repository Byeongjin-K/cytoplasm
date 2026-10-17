"""What the generic path refuses: owner-only actions and effects a dedicated path owns.

The generic path exists so that no request ends with "no approval path". Two kinds of
request still do not belong on it, and both are refused with a pointer instead of a card:

- owner-only actions (``OWNER_ONLY``): things an approval card cannot stand in for —
  credentials, payment, identity checks, pressing an approval itself. Only the owner does
  these, by hand.
- effects that already have a dedicated approval path (``DEDICATED``): mail, calendar,
  tasks, Obsidian, Drive sharing, patent export. Their cards carry checks the generic
  card does not (duplicate-send guard, recipient verification, read-back), so a command
  here that would perform one of them is sent back to that CLI.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Refusal:
    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class _Rule:
    code: str
    pattern: re.Pattern[str]
    reason: str


def _rule(code: str, pattern: str, reason: str) -> _Rule:
    return _Rule(code, re.compile(pattern, re.IGNORECASE), reason)


OWNER_ONLY: Final[tuple[_Rule, ...]] = (
    _rule("credential", r"passw(?:or)?d|비밀번호|암호\s*변경|\bchpasswd\b|\bssh-keygen\b|토큰\s*(?:재)?발급|api[_ -]?key",
          "비밀번호·키·토큰 같은 자격증명 변경은 형이 직접 한다"),
    _rule("payment", r"결제|송금|이체|구매|주문|카드\s*번호|\bpayment\b|\bcheckout\b|\bpurchase\b|\binvoice\s+pay",
          "결제·송금·구매는 형이 직접 한다"),
    _rule("identity", r"\b(?:2fa|otp|mfa)\b|인증\s*(?:코드|번호)|본인\s*인증|\boauth\b|\bconsent\b|"
          r"\bgws\s+auth\b|\bhermes\s+(?:login|auth)\b|\bgh\s+auth\b",
          "로그인·2단계 인증·OAuth 동의는 형이 직접 한다"),
    _rule("privilege", r"(?:^|\s)(?:sudo|su|doas|visudo|passwd)(?:\s|$)",
          "관리자 권한(sudo) 작업은 형이 직접 한다"),
    _rule("self-approval", r"/reactions/|✅|%E2%9C%85|white_check_mark",
          "승인 반응을 대신 누르는 일은 할 수 없다 — 승인은 형의 ✅ 뿐이다"),
)

DEDICATED: Final[tuple[_Rule, ...]] = (
    _rule("mail", r"\bmailon(?:\.main)?\b.*\bsend\b|\bgws\s+gmail\b.*\+send|\bsendmail\b|\bsmtplib\b",
          "메일은 메일 스킬 compose 로 보낸다(숨은참조는 --bcc/--bcc-file)"),
    _rule("calendar", r"\bgws\s+calendar\s+events\s+(?:insert|create|update|patch|delete|quickadd|import|move)\b",
          "일정은 캘린더 스킬로 요청한다"),
    _rule("tasks", r"\bgws\s+tasks\s+tasks\s+(?:insert|patch|update|delete|move|clear)\b",
          "할 일은 todo 스킬 request 로 요청한다"),
    _rule("obsidian", r"\bobsidian_write\b|\bgit\b.*\bpush\b.*(?:vault|obsidian)",
          "Obsidian 노트는 python3 -m automation.obsidian_write request 로 요청한다"),
    _rule("drive-share", r"\bgws\s+drive\s+permissions\s+(?:create|update)\b",
          "Drive 파일을 사람에게 공유하려면 메일 compose --drive-share --share-with 를 쓴다"),
    _rule("drive-upload", r"\bgws\s+drive\s+(?:files\s+create|\+upload)\b|\bdrive_publish_cli\b",
          "Drive 저장은 이 경로의 request drive-save 로 요청한다"),
    _rule("patent", r"patent[-_]export|patent-drafts", "특허 반출은 patent-prep 스킬의 전용 반출로만 한다"),
)


def refusal(description: str, argv: tuple[str, ...] = ()) -> Refusal | None:
    """The first rule the request hits, owner-only first; ``None`` when the card may be posted."""
    text = f"{description}\n{shlex.join(argv)}" if argv else description
    for rule in OWNER_ONLY:
        if rule.pattern.search(text):
            return Refusal(f"owner-only:{rule.code}", rule.reason)
    command = shlex.join(argv)
    for rule in DEDICATED:
        if command and rule.pattern.search(command):
            return Refusal(f"dedicated:{rule.code}", rule.reason)
    return None

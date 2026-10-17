"""Outbound guard: an agent reply may not ask the owner to ✅ a chat message.

Approval in this system is a reaction on a standard approval card, bound to a hash
and consumed by a watcher. A ✅ on an ordinary reply is read by nothing. On 2026-10-07
an agent with no approval path for the request improvised a draft and wrote "please ✅
this message"; the owner did, and nothing ran. Prompts cannot make that impossible, so
the gateway's ``transform_llm_output`` hook runs this check on every reply: a line that
asks for a ✅ while the reply carries no approval-card link is replaced by a statement
that the work has no approval path. Replies that cite a card link pass unchanged.
"""

from __future__ import annotations

import re
from typing import Final

from automation.interop.thread_pointer import MARKER

_CHECK: Final = r"(?:✅|:white_check_mark:)"
_ASK: Final = re.compile(
    rf"{_CHECK}[^\n]{{0,24}}(?:승인|눌러|반응|달아|확인)[^\n]{{0,24}}"
    r"(?:주십시오|주세요|주시면|부탁|해 ?줘|해주|바랍니다)"
    rf"|(?:승인|눌러|반응|달아)[^\n]{{0,24}}{_CHECK}[^\n]{{0,24}}"
    r"(?:주십시오|주세요|주시면|부탁|해 ?줘|해주|바랍니다)"
    rf"|(?:react|approve)[^\n]{{0,40}}{_CHECK}|{_CHECK}[^\n]{{0,40}}(?:to approve|this message)",
    re.IGNORECASE,
)
_CARD_LINK: Final = re.compile(rf"https://(?:\w+\.)?discord(?:app)?\.com/channels/\d+/\d+|{re.escape(MARKER)}\s")
NOTICE: Final = (
    "⚠ 이 작업은 표준 승인 경로를 거치지 않았습니다 — 승인 카드가 게시되지 않았으므로 "
    "채팅 메시지의 ✅는 승인으로 처리되지 않고 아무것도 실행되지 않습니다. "
    "전용 스킬 명령이 없는 작업도 'python3 -m automation.effect_request request' 로 "
    "승인 카드를 올린 뒤 그 카드 링크로 다시 안내해야 합니다."
)


def guard(text: str) -> str:
    """The reply unchanged, or with every chat-✅ request line replaced by ``NOTICE``."""
    if _CARD_LINK.search(text) or not _ASK.search(text):
        return text
    kept = [line for line in text.splitlines() if not _ASK.search(line)]
    return "\n".join((*kept, "", NOTICE)).strip()

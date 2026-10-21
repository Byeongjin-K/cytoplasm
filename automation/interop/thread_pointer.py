"""승인 요청 CLI 가 에이전트에게 넘기는 승인 스레드 좌표 한 줄의 단일 정의.

승인 카드를 게시한 CLI 는 이 줄을 stdout 에 내고, 에이전트는 소유자 답장에 그 마스킹 링크를 그대로
싣는다. 줄이 없으면 에이전트는 "승인 스레드에 게시했다" 고만 말하고 소유자가 스레드를 직접
찾아야 한다(2026-09-23 mail·todo 실측). 좌표는 `owner_message.discord_link`, 표시 문구는
`owner_message.masked_discord_link` 로 만들고,
좌표를 모르면 추측하지 않고 생산자가 준 값을 검색 키로 넘긴다.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from .owner_message import LinkStatus, discord_link, masked_discord_link

MARKER: Final = "APPROVAL-THREAD"


def _coordinate(record: Mapping[str, object], field: str) -> str | None:
    value = record.get(field)
    return value if isinstance(value, str) and value else None


def approval_thread_line(key: str, value: str, record: Mapping[str, object]) -> str:
    link = discord_link(
        space="guild",
        channel_id=_coordinate(record, "approval_thread_id"),
        guild_id=_coordinate(record, "approval_guild_id"),
        message_id=_coordinate(record, "message_id"),
    )
    target = (
        f"url={masked_discord_link(link.url, '승인스레드')}"
        if link.status is LinkStatus.AVAILABLE and link.url is not None
        else f"url=unavailable search={value}"
    )
    return f"{MARKER} {key}={value} {target}"

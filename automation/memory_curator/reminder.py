"""대기 중인 승인을 다시 떠오르게 하는 정책 — 순수 함수.

승격 확인은 한 번 게시되고 끝이다. 재배치는 메시지가 사라지면 재게시하고 공급망
워처는 매 tick 다시 확인하는데, 이쪽만 스크롤 밖으로 밀리면 접근 불가였다. 2026-08-03
실측: 미처리 2건이 DM 최신에서 51번째·157번째였고 소유자가 처리한 4건은 20~24번째였다
— **보이는 것은 전부 처리됐고 안 보이는 것만 남아 있었다**.

이 모듈이 하지 않는 일이 그 설계다. **승인 메시지를 건드리지 않는다.** 지웠다 다시
올리면 그 사이 소유자가 누른 반응을 잃을 수 있고(이 리포가 반복해 고쳐온 실패 양식),
승인 메시지 단일성 규칙도 위태롭다. 리마인더는 승인이 아니라 알림이며, ✅·⛔ 를 달지
않는다 — 달면 그 순간 두 번째 승인 표면이 된다. 가리키기만 한다.

정책은 소유자 시계를 따른다: 3시간마다, 단 밤 12시부터 오전 9시 사이에는 보내지
않는다. 조용한 창에 걸린 리마인더는 취소가 아니라 연기다 — ``last_sent`` 를 전진시키는
것은 실제로 보냈을 때뿐이므로, 창이 열리면 바로 나간다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from automation.interop.owner_message import Ref

#: 정책은 소유자가 사는 시계로 판단한다. 저장은 UTC 이고 변환만 여기서 한다.
OWNER_TIMEZONE: Final = "Asia/Seoul"

#: 대기 중인 승인을 다시 알리는 간격.
REMINDER_INTERVAL: Final = timedelta(hours=3)

#: 알리지 않는 창 — 밤 12시부터 오전 9시(소유자 지시, 2026-08-03). 끝은 열린 구간이라
#: 9시 정각은 조용한 시간이 아니다.
QUIET_FROM_HOUR: Final = 0
QUIET_UNTIL_HOUR: Final = 9


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """리마인더가 가리킬 승인 하나 — 무엇을, 어디서 볼 수 있는지."""

    draft_id: str
    source_file: str
    preview: str
    ref: Ref


def in_quiet_window(now: datetime) -> bool:
    """소유자 시계로 밤 12시~오전 9시인가."""
    return QUIET_FROM_HOUR <= now.astimezone(ZoneInfo(OWNER_TIMEZONE)).hour < QUIET_UNTIL_HOUR


def due(
    now: datetime,
    *,
    last_sent: datetime | None,
    pending: tuple[PendingApproval, ...],
) -> bool:
    """지금 리마인더를 보내야 하는가.

    보낼 것이 없으면 보내지 않는다 — 빈 알림은 다음 알림의 신뢰를 깎는다.
    """
    if not pending:
        return False
    if in_quiet_window(now):
        return False
    if last_sent is None:
        return True
    return now - last_sent >= REMINDER_INTERVAL


def render(pending: tuple[PendingApproval, ...]) -> str:
    """소유자가 스크롤로 찾지 못한 것이 문제였으므로, 링크가 본문이다."""
    from automation.interop.owner_message import discord_link, masked_discord_link

    from .notice import ReminderContent

    lines = [f"🔔 승인 대기 {len(pending)}건 — 아래 링크에서 처리하세요(이 알림 자체는 승인이 아닙니다)."]
    facts = lines.copy()
    references: list[Ref] = []
    for item in pending:
        ref = item.ref
        link = discord_link(space=ref.space, channel_id=ref.channel_id,
                            message_id=ref.message_id, guild_id=ref.guild_id)
        target = (masked_discord_link(link.url, "승인 카드") if link.url
                  else " / ".join(ref.search or ("Discord 검색", item.draft_id)))
        entry = f"- `{item.draft_id}` [{item.source_file}] '{item.preview}'"
        lines.append(f"{entry} → {target}")
        facts.append(entry if ref.scope == "resource" else lines[-1])
        if ref.scope == "resource":
            references.append(ref)
    content = "\n".join(lines)
    return ReminderContent(content, "\n".join(facts), tuple(references)) if references else content

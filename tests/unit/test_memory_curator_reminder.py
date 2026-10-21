"""대기 중인 승인은 스크롤 밖으로 밀리면 다시 떠오르지 않았다 — 그 리마인더의 정책.

2026-08-03 실측. 소유자가 승격 확인 6건 중 4건을 처리했는데, 미처리 2건은 DM 최신에서
**51번째·157번째**였다. 처리된 4건은 20~24번째였다. 즉 **보이는 것은 전부 처리됐고 안
보이는 것만 남아 있었다** — 안 누른 게 아니라 닿지 않은 것이다. 승격 확인은 한 번
게시되고 끝이라(재배치는 사라지면 재게시하고 공급망 워처는 매 tick 다시 확인한다)
레코드는 영원히 기다리는데 소유자는 존재조차 몰랐다.

여기서 고정하는 것은 **정책**이다. 리마인더는 승인 메시지를 건드리지 않는다 —
지웠다 다시 올리면 그 순간 소유자가 누른 반응을 잃을 수 있고(이 리포가 반복해서
고쳐온 실패 양식), 승인 메시지 단일성 규칙도 위태롭다. 별도 알림으로 가리키기만 한다.

주기는 3시간, 그리고 **밤 12시부터 오전 9시 사이에는 보내지 않는다**(소유자 지시).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from automation.interop.owner_message import Ref
from automation.memory_curator.reminder import (
    OWNER_TIMEZONE,
    PendingApproval,
    due,
    render,
)

_KST = ZoneInfo(OWNER_TIMEZONE)


def _at(hour: int, minute: int = 0, day: int = 3) -> datetime:
    """소유자 시각(KST)으로 지정하되 저장은 UTC 다 — 정책은 소유자 시계를 따른다."""
    return datetime(2026, 8, day, hour, minute, tzinfo=_KST).astimezone(UTC)


def _pending(count: int = 1) -> tuple[PendingApproval, ...]:
    return tuple(
        PendingApproval(
            draft_id=f"d{index}",
            source_file="USER.md",
            preview="어떤 판단 근거",
            ref=Ref(scope="message", space="dm", channel_id="1", message_id=str(index + 1)),
        )
        for index in range(count)
    )


def test_nothing_pending_is_never_a_reminder() -> None:
    assert due(_at(10), last_sent=None, pending=()) is False


def test_a_first_reminder_is_due_when_something_waits() -> None:
    assert due(_at(10), last_sent=None, pending=_pending()) is True


def test_within_three_hours_it_holds() -> None:
    last = _at(10)
    assert due(last + timedelta(hours=2, minutes=59), last_sent=last, pending=_pending()) is False


def test_at_three_hours_it_is_due() -> None:
    last = _at(10)
    assert due(last + timedelta(hours=3), last_sent=last, pending=_pending()) is True


def test_the_quiet_window_holds_even_when_long_overdue() -> None:
    """밤 12시~오전 9시. 자고 있는 사람을 깨우는 알림은 알림이 아니라 소음이다."""
    last = _at(20, day=2)
    for hour in (0, 1, 5, 8):
        assert due(_at(hour), last_sent=last, pending=_pending()) is False, hour


def test_the_window_opens_at_nine_and_closes_at_midnight() -> None:
    last = _at(20, day=2)
    assert due(_at(9), last_sent=last, pending=_pending()) is True, "9시는 조용한 시간이 아니다"
    assert due(_at(23, 59), last_sent=last, pending=_pending()) is True
    assert due(_at(0, 0, day=4), last_sent=last, pending=_pending()) is False


def test_a_reminder_deferred_by_the_quiet_window_is_not_lost() -> None:
    """조용한 시간에 걸러진 건 취소가 아니라 연기다 — 창이 열리면 바로 나간다."""
    last = _at(20, day=2)
    assert due(_at(3), last_sent=last, pending=_pending()) is False
    assert due(_at(9, 1), last_sent=last, pending=_pending()) is True


def test_the_reminder_names_the_file_and_links_the_message() -> None:
    """소유자가 스크롤로 찾지 못한 것이 문제였다 — 링크가 없으면 같은 문제다."""
    text = render(_pending(2))
    assert "USER.md" in text
    assert "어떤 판단 근거" in text
    assert "[승인 카드](<https://discord.com/channels/@me/1/1>)" in text
    assert "[승인 카드](<https://discord.com/channels/@me/1/2>)" in text
    assert "2건" in text


def test_the_reminder_says_it_is_not_itself_an_approval() -> None:
    """알림에 ✅ 를 달면 두 번째 승인 표면이 된다 — 단일성 규칙이 금지하는 것."""
    text = render(_pending())
    assert "✅" not in text and "⛔" not in text

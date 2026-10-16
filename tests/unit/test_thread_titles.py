"""Every bot-opened thread starts with a big emoji title and an emoji-led name.

Owner 2026-10-16: "캘린더 다이제스트도 기관메일 다이제스트처럼 제일 처음에 좀 큼지막하게
제목을 써서 이모지와 함께 앞으로 모든 이런 스레드에는 제목을 좀 크게 붙여줘".
"""
from __future__ import annotations

import pytest

from automation.interop import origin_notice
from automation.interop.approval_surface import (
    KIND_ICONS,
    KIND_LABELS,
    ApprovalKind,
    RequestThread,
    request_thread_name,
    request_thread_notice,
)


@pytest.mark.parametrize("kind", list(ApprovalKind))
def test_every_kind_has_an_icon_that_is_not_a_decision_emoji(kind: ApprovalKind) -> None:
    icon = KIND_ICONS[kind]

    assert icon and icon not in {"✅", "⛔", "⌛"}
    assert request_thread_name(kind, RequestThread(title="x")).startswith(f"{icon} {KIND_LABELS[kind]} · ")


@pytest.mark.parametrize("kind", list(ApprovalKind))
def test_the_request_announcement_opens_with_the_big_title(kind: ApprovalKind) -> None:
    # Given any request: the announcement is the thread's starter message.
    first, _, rest = request_thread_notice(kind, RequestThread(title="draft 7")).partition("\n")

    # Then its first line is the big title and the envelope below it is unchanged.
    assert first == f"## {KIND_ICONS[kind]} {KIND_LABELS[kind]} · draft 7"
    assert rest and not rest.startswith("#")
    assert "승인 대기" in rest


def test_the_calendar_digest_thread_is_titled_like_the_mail_digest() -> None:
    request = RequestThread(title="다이제스트 2026-10-16")

    assert request_thread_name(ApprovalKind.CALENDAR, request) == "🗓️ 캘린더 · 다이제스트 2026-10-16"
    assert request_thread_notice(ApprovalKind.CALENDAR, request).splitlines()[0] == (
        "## 🗓️ 캘린더 · 다이제스트 2026-10-16"
    )


@pytest.mark.parametrize(("name", "icon"), [
    ("회의록 처리", "🎙️"), ("메모리 정리", "🧠"), ("대리 요청 처리 확인", "🙋"), ("음성 메시지", "🎤"),
    ("메일: 세미나 (draft a1)", "✉️"), ("할일 등록", "📋"), ("rec-123", "🧵"),
])
def test_result_threads_open_with_an_icon_and_a_title(name: str, icon: str) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def api(method: str, path: str, payload: dict | None = None) -> dict:
        calls.append((method, path, payload))
        return {"id": "9"}

    thread = origin_notice.resolve_thread_id(api, origin_notice.OriginRef("1", "2"), name)

    assert thread == "9"
    assert calls == [
        ("POST", "/channels/1/messages/2/threads", {"name": f"{icon} {name}"}),
        ("POST", "/channels/9/messages", {"content": f"## {icon} {name}"}),
    ]


def test_a_result_thread_whose_title_fails_still_receives_the_notice() -> None:
    def api(method: str, path: str, payload: dict | None = None) -> dict:
        if path == "/channels/9/messages":
            raise OSError("title refused")
        return {"id": "9"}

    assert origin_notice.resolve_thread_id(api, origin_notice.OriginRef("1"), "회의록 처리") == "9"

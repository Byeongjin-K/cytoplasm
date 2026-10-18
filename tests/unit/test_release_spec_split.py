"""상세 메시지 분할을 고정한다 — 오늘의 조각 수·길이, 그리고 URL 을 자르는 자리.

`split_messages` 의 결과 개수 n 은 카드가 「변경 상세 아래 n개 메시지」라고 약속한 바로 그
수다. 저장된 레코드가 재생하는 v3 카드는 렌더할 때마다 이 함수를 다시 부르므로, n 이 하루라도
달라지면 이미 게시된 카드가 거짓말이 된다. 분할기를 건드리기 전에 그 계약을 여기에 먼저 못
박고(특성화), 그 위에서 URL 이 두 통으로 찢기는 결함만 고친다.
"""
from __future__ import annotations

from typing import Final

import pytest

from automation.release_spec import NEW_RENDER_VERSION, ReleaseSpec, spec_from_record
from automation.release_spec_message import MESSAGE_LIMIT, detail_header, split_messages

_VERSION: Final = "v1.2.3"
_HEAD: Final = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
#: 한 줄이 예산을 넘겨 줄 안에서 잘려야만 하는 본문에서 쓰는 예산 — 머리글과 개행을 뺀 나머지.
_BUDGET: Final = MESSAGE_LIMIT - len(detail_header(_VERSION, _HEAD, 2, 2)) - 1
_URL: Final = "https://example.test/" + "b" * 40


def _split(body: str, *, preserve_urls: bool = False) -> tuple[str, ...]:
    """저장된 판본의 재생은 기본값 그대로, URL 보호는 신규 판본(v6)만 켠다."""
    return split_messages(
        version=_VERSION, head_sha=_HEAD, body=body, preserve_urls=preserve_urls
    )


def _bodies(messages: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(message.split("\n", 1)[1] for message in messages)


def test_a_short_body_renders_exactly_one_headed_message() -> None:
    # Given: 커밋이 없는 릴리스가 만드는 가장 짧은 본문.
    body = "### 공통·기타\n- 커밋 없음"
    # When
    messages = _split(body)
    # Then: 한 통이고, 머리글 한 줄 뒤에 본문이 그대로 붙는다.
    assert messages == (
        f"[release] {_VERSION} 변경 상세 (1/1) — 기준 {_HEAD[:12]}\n{body}",
    )


def test_a_sixty_commit_body_keeps_three_messages_of_the_pinned_lengths() -> None:
    # Given: 승인 카드 픽스처와 같은 모양의 60 커밋 본문.
    body = "\n".join(f"- 변경 {index} " + "가" * 60 for index in range(60))
    # When
    messages = _split(body)
    # Then: 2026-09-18 실측 그대로 — 세 통, 같은 길이, 한 줄도 잃지 않는다.
    assert tuple(len(message) for message in messages) == (1899, 1840, 529)
    assert "\n".join(_bodies(messages)) == body


def test_a_line_longer_than_one_message_keeps_its_pinned_three_way_split() -> None:
    # Given: 한 통보다 긴 줄 하나를 가진 본문 — 줄 안에서 잘릴 수밖에 없다.
    body = "### repo\n- " + "긴제목" * 900 + " (000000000001)"
    # When
    messages = _split(body)
    # Then: 2026-09-18 실측 그대로 — 세 통, 같은 길이, 글자는 순서대로 모두 남는다.
    assert tuple(len(message) for message in messages) == (55, 1900, 911)
    assert "".join(_bodies(messages)).replace("\n", "") == body.replace("\n", "")


def test_a_url_crossing_the_message_boundary_stays_whole_in_one_message() -> None:
    # Given: 예산 경계를 가로지르는 URL 하나가 붙은 긴 줄 (릴리스 본문의 이슈·PR 링크).
    body = "가" * (_BUDGET - 10) + _URL
    # When
    messages = _split(body, preserve_urls=True)
    # Then: URL 은 정확히 한 통 안에 통째로 들어간다 — 두 통으로 찢기면 아무 데도 안 걸린다.
    assert sum(_URL in message for message in messages) == 1


def test_a_url_crossing_the_message_boundary_keeps_two_messages_in_the_limit() -> None:
    # Given: 위와 같은 본문 — 카드가 약속한 통 수는 URL 보호 때문에 달라져선 안 된다.
    body = "가" * (_BUDGET - 10) + _URL
    # When
    messages = _split(body, preserve_urls=True)
    # Then: 여전히 두 통이고, 두 통 모두 한도 안이며, 글자는 하나도 사라지지 않는다.
    assert len(messages) == 2
    assert all(len(message) <= MESSAGE_LIMIT for message in messages)
    assert "".join(_bodies(messages)).replace("\n", "") == body


@pytest.mark.parametrize("render_version", [3, 4])
def test_stored_cards_replay_the_original_two_detail_messages(render_version: int) -> None:
    # Given: a saved card whose old fixed-width splitter promised exactly two parts.
    body = "가" * (_BUDGET - 10) + _URL + " " + "다" * (_BUDGET + 9 - len(_URL))
    record = {
        "version": _VERSION, "head_sha": _HEAD, "release_nonce": "f" * 32,
        "surface_digests": "[]", "patch_notes": body,
        "render_version": str(render_version),
    }
    expected = tuple(
        f"{detail_header(_VERSION, _HEAD, index, 2)}\n{part}"
        for index, part in enumerate((body[:_BUDGET], body[_BUDGET:]), start=1)
    )
    # When: the stored record, not a fresh default spec, is replayed.
    replay = spec_from_record(record)
    # Then: both the count and every detail byte retain the old contract.
    assert replay.detail_messages() == expected


def test_new_cards_version_the_url_safe_detail_contract() -> None:
    # Given: preserving this URL costs an extra part, on top of the bundle list and the
    # backlink budget that the new card version already adds to every detail message.
    body = "가" * (_BUDGET - 10) + _URL + " " + "다" * (_BUDGET + 9 - len(_URL))
    # When: a new release request chooses its default rendering contract.
    spec = ReleaseSpec(_VERSION, _HEAD, "f" * 32, (), body, render_version=6)
    # Then: the new policy is explicit and each emitted part satisfies its budget.
    assert spec.render_version == 6
    assert NEW_RENDER_VERSION == 7
    messages = spec.detail_messages()
    assert len(messages) == 4
    assert sum(_URL in message for message in messages) == 1
    assert all(len(message) <= MESSAGE_LIMIT for message in messages)

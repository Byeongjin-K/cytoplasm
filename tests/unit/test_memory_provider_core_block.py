from __future__ import annotations

import pytest

from automation.memory_provider import core_block

RELOCATED = {
    "key": "obsidian:000_PARA/Resource/person--0123456789ab.md",
    "title": "합성 동료 연락처",
    "text": "---\ntitle: x\n---\n# 머리\n\n## 원본 기록 (verbatim)\n\n합성 동료 내선은\n  1234 이다.\n---\nprovenance: observed",
}
WIKI = {"key": "wiki:선호-합성.md", "title": "선호: 합성", "text": "본문 (자체 메모리에서 자동 승격 2026-10-14) 끝"}
DIGEST = {"key": "note:_weekly/notes-2026-W30.md", "title": "notes-2026-W30", "text": "# 주간 노트 정리\n\n- 항목"}


def test_compact_keeps_only_the_verbatim_record_of_a_relocated_note() -> None:
    assert core_block.compact(RELOCATED) == "[합성 동료 연락처] 합성 동료 내선은 1234 이다."


def test_compact_strips_the_promotion_mark_from_a_wiki_note() -> None:
    assert core_block.compact(WIKI) == "[선호: 합성] 본문 끝"


def test_title_line_adds_the_first_body_line_unless_the_title_already_holds_it() -> None:
    assert core_block.title_line(DIGEST) == "- notes-2026-W30 — 주간 노트 정리"
    same = {"key": "note:a.md", "title": "메모리 승격: 주간 노트 정리 모음", "text": "# 주간 노트 정리"}
    assert core_block.title_line(same) == "- 주간 노트 정리 모음"


def test_build_puts_personal_notes_first_then_digest_titles() -> None:
    assert core_block.build([DIGEST, RELOCATED, WIKI]) == (
        "[합성 동료 연락처] 합성 동료 내선은 1234 이다.\n[선호: 합성] 본문 끝\n- notes-2026-W30 — 주간 노트 정리")


def test_over_the_cap_digest_titles_go_first_then_notes_fall_back_to_titles_from_the_end() -> None:
    notes = [RELOCATED, WIKI, DIGEST]
    no_digest = core_block.build(notes, cap=len(core_block.build(notes)) - 1)
    assert "notes-2026-W30" not in no_digest
    assert core_block.build(notes, cap=len(no_digest) - 1).endswith("\n[선호: 합성]")


def test_core_notes_cache_refetches_after_ten_minutes_and_retries_failures_after_one() -> None:
    now = [0.0]
    answers: list[object] = [RuntimeError("down"), [WIKI], [RELOCATED]]
    calls: list[float] = []

    def fetch() -> list[dict[str, str]]:
        calls.append(now[0])
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[return-value]

    cache = core_block.CoreNotes(fetch, clock=lambda: now[0], spawn=lambda work: work())
    assert cache.notes() == ()
    now[0] = 30.0
    assert cache.notes() == ()
    now[0] = 61.0
    assert cache.personal_keys() == frozenset({"wiki:선호-합성.md"})
    now[0] = 61.0 + core_block.CACHE_SECONDS - 1
    assert cache.block().startswith(core_block.HEADER + "\n[선호: 합성]")
    now[0] = 61.0 + core_block.CACHE_SECONDS
    assert cache.personal_keys() == frozenset({RELOCATED["key"]})
    assert calls == [0.0, 61.0, 61.0 + core_block.CACHE_SECONDS]


def test_a_stale_cache_answers_at_once_and_refreshes_in_the_background() -> None:
    now = [0.0]
    pending: list[object] = []
    answers = [[WIKI], [RELOCATED]]
    cache = core_block.CoreNotes(lambda: answers.pop(0), clock=lambda: now[0], spawn=pending.append)
    assert cache.personal_keys() == frozenset({WIKI["key"]})
    now[0] = core_block.CACHE_SECONDS + 1
    assert cache.personal_keys() == frozenset({WIKI["key"]})
    assert cache.personal_keys() == frozenset({WIKI["key"]})
    assert len(pending) == 1
    pending.pop()()  # type: ignore[operator]
    assert cache.personal_keys() == frozenset({RELOCATED["key"]})


def test_an_empty_store_gives_no_block() -> None:
    assert core_block.CoreNotes(lambda: []).block() == ""


@pytest.mark.parametrize("key,personal", [("wiki:a.md", True), ("obsidian:000_PARA/Resource/x--0123456789ab.md", True),
                                          ("note:_weekly/a.md", False)])
def test_personal_notes_are_wiki_and_relocated(key: str, personal: bool) -> None:
    assert core_block.is_personal(key) is personal

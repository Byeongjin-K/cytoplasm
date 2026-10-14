from __future__ import annotations

import pytest

from automation.memory_provider.attach import Recall, attach

FENCED = "<memory-context>\n기억\n</memory-context>"


def _ready(query: str = "배양기 점검표") -> Recall:
    recall = Recall(query, block="기억")
    recall.done.set()
    return recall


def _owner(text: str) -> dict[str, object]:
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


@pytest.mark.parametrize("state", ["running", "delivered", "empty"])
def test_nothing_changes_until_a_deferred_pick_has_a_result(state: str) -> None:
    recall = Recall("배양기 점검표")
    if state != "running":
        recall.done.set()
        recall.block = "" if state == "empty" else "기억"
        recall.delivered = state == "delivered"

    assert attach({"input": [_owner("배양기 점검표")]}, recall, FENCED) is None


def test_the_note_keeps_its_position_on_later_calls_of_the_same_turn() -> None:
    recall = _ready()
    first_items = [_owner("배양기 점검표 알려줘"), {"type": "function_call_output", "output": "1"}]

    first = attach({"input": first_items}, recall, FENCED)
    later = attach({"input": [*first_items, {"type": "function_call_output", "output": "2"}]}, recall, FENCED)

    assert first is not None and later is not None
    assert first["input"][2] == {"role": "user", "content": [{"type": "input_text", "text": FENCED}]}
    assert later["input"][2] == first["input"][2]
    assert later["input"][3] == {"type": "function_call_output", "output": "2"}


def test_a_new_turn_is_never_touched() -> None:
    assert attach({"input": [_owner("전혀 다른 일")]}, _ready(), FENCED) is None


def test_chat_messages_get_a_plain_text_note() -> None:
    changed = attach({"messages": [{"role": "user", "content": "배양기 점검표?"}]}, _ready(), FENCED)

    assert changed is not None
    assert changed["messages"][-1] == {"role": "user", "content": FENCED}

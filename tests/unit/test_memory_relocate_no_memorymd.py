"""New destination regressions live separately to leave FS3-pinned case counts intact."""
from __future__ import annotations

from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.curator import parse_memory_file
from automation.memory_relocate.discover import select_batch
from automation.memory_relocate.plan import build_relocation_plan


def test_agent_rule_stays_native_without_a_move_proposal() -> None:
    # Given a USER.md handling rule classified for the retired MEMORY.md destination.
    text = "할 일 등록은 기본 할 일 목록을 사용한다."
    files = {"user": parse_memory_file(text, kind="user")}
    verdict = EntryVerdict("user", text, "AGENT_RULE", text, "rule", None, True)

    # When new relocation candidates are selected.
    candidates = select_batch((verdict,), files, frozenset())

    # Then no card offers a move for that line.
    assert candidates == ()


def test_person_reference_still_proposes_a_000_para_person_note() -> None:
    # Given a USER.md person reference.
    text = "가상동료의 호칭은 선임님이다."
    files = {"user": parse_memory_file(text, kind="user")}
    verdict = EntryVerdict("user", text, "PERSON_REFERENCE", text, "person", None, True)

    # When the candidate is selected and its destination is planned.
    (found,) = select_batch((verdict,), files, frozenset())
    plan = build_relocation_plan(
        found.candidate.entry_text,
        source_kind=found.candidate.source_kind,
        destination=found.destination,
    )

    # Then the same person entry goes to the existing 000_PARA person-note destination.
    assert found.candidate.entry_text == text
    assert found.destination == "person"
    assert plan.note_plan.relpath.parts[0] == "000_PARA"
    assert plan.note_plan.relpath.name.startswith("person--")

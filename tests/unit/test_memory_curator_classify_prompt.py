from __future__ import annotations

from automation.memory_curator.classify_model import ROUTES
from automation.memory_curator.classify_prompt import (
    MC_CLASSIFY_VERSION,
    SCHEMA_KEYS,
    render,
)

_ENTRY_A = (
    "배포 판정은 readlink /srv/autophagy-skills/live/<skill> 해시로 한다.\n"
    "모델 프록시는 127.0.0.1:4000 에서 듣는다."
)
_ENTRY_B = "cha에게 보고할 때는 항상 한국어로 쓴다."


def test_version_tag_opens_the_prompt() -> None:
    # Given the frozen version constant / When one entry is rendered
    rendered = render(_ENTRY_A, source_kind="memory")

    # Then the first line carries the version tag downstream parsers pin.
    assert MC_CLASSIFY_VERSION == "mc-classify-v2"
    assert MC_CLASSIFY_VERSION in rendered.splitlines()[0]


def test_every_shared_route_name_is_named_and_defined() -> None:
    # Given the shared route table / When one entry is rendered
    rendered = render(_ENTRY_A, source_kind="memory")

    # Then no route can drift out of the prompt unnoticed.
    for route in ROUTES:
        assert route in rendered
    assert rendered.count("ROUTES:") == 1
    for route in ROUTES:
        definition_line = next(
            line for line in rendered.splitlines() if line.startswith(f"- {route} =")
        )
        assert len(definition_line) > len(f"- {route} = ")


def test_output_schema_is_exactly_three_keys() -> None:
    # Given the frozen response contract / When one entry is rendered
    rendered = render(_ENTRY_A, source_kind="memory")

    # Then the SCHEMA line declares the three contract keys and no fourth:
    # exactly one `": "` separator exists per declared key.
    schema_line = next(
        line for line in rendered.splitlines() if line.startswith("SCHEMA: ")
    )

    assert SCHEMA_KEYS == ("route", "evidence", "reason")
    for key in SCHEMA_KEYS:
        assert f'"{key}": ' in schema_line
    assert schema_line.count('": "') == len(SCHEMA_KEYS)
    assert "FORBIDDEN-KEYS: confidence" in rendered


def test_json_only_and_evidence_directives_are_present() -> None:
    # Given the machine-parsed response / When one entry is rendered
    rendered = render(_ENTRY_A, source_kind="memory")

    # Then the JSON-only and verbatim-evidence directives are pinned tokens,
    # and the prompt itself carries no markdown fence to imitate.
    assert "OUTPUT: JSON-ONLY" in rendered
    assert "EVIDENCE: VERBATIM>=8" in rendered
    assert "```" not in rendered


def test_entry_is_embedded_verbatim_between_markers_with_its_source_kind() -> None:
    # Given a multi-line entry / When it is rendered for each memory kind
    memory_rendered = render(_ENTRY_A, source_kind="memory")
    user_rendered = render(_ENTRY_B, source_kind="user")

    # Then the entry survives byte-for-byte inside unambiguous delimiters.
    assert f"\n<<<ENTRY\n{_ENTRY_A}\nENTRY>>>\n" in memory_rendered
    assert "SOURCE_KIND: memory" in memory_rendered
    assert f"\n<<<ENTRY\n{_ENTRY_B}\nENTRY>>>\n" in user_rendered
    assert "SOURCE_KIND: user" in user_rendered


def test_render_is_byte_identical_across_calls() -> None:
    # Given identical arguments / When render is called twice
    first = render(_ENTRY_A, source_kind="memory")
    second = render(_ENTRY_A, source_kind="memory")

    # Then nothing time- or randomness-dependent leaked into the prompt.
    assert first == second


def test_render_carries_only_its_own_entry() -> None:
    # Given two distinct entries / When each is rendered separately
    rendered_a = render(_ENTRY_A, source_kind="memory")
    rendered_b = render(_ENTRY_B, source_kind="user")

    # Then neither prompt leaks the other entry's text.
    assert _ENTRY_B not in rendered_a
    assert _ENTRY_A not in rendered_b

"""Contract for the Hermes native-memory curator (pure logic).

Hermes writes ambient facts to MEMORY.md (cap 3300 chars) and USER.md
(cap 3000 chars, both from automation.hermes_memory_config.LIMITS), separated by lines that are just ``§``.  When a file
fills up, Hermes silently rejects new writes (no auto-organization).  The
curator keeps those files tidy and under cap:

* **autonomous, lossless** compaction (dedupe exact duplicates, trim
  per-entry whitespace) — never loses information, so it is safe to apply
  without owner approval (cha decision 2026-07-29: option (a));
* **owner-gated** relocation — durable judgment (principle/decision/
  preference) is only *flagged* as a promotion candidate for the decision
  twin; the curator never removes or promotes an entry on its own.

This test pins the pure logic only (no file/network side effects).
"""

from __future__ import annotations

from automation.memory_curator import (
    CurationPlan,
    MemoryEntry,
    MemoryFile,
    curate,
    parse_memory_file,
    serialize_memory_file,
)

# --------------------------------------------------------------------------- #
# Parsing / serialization
# --------------------------------------------------------------------------- #
def test_parse_splits_on_section_marker() -> None:
    mf = parse_memory_file("Entry A\n§\nEntry B\n§\nEntry C", kind="memory")
    assert mf.kind == "memory"
    assert [e.text for e in mf.entries] == ["Entry A", "Entry B", "Entry C"]


def test_parse_trims_and_drops_blank_entries() -> None:
    mf = parse_memory_file("  A  \n§\n\n§\n  \n§\nB", kind="user")
    assert [e.text for e in mf.entries] == ["A", "B"]


def test_kind_selects_cap() -> None:
    assert parse_memory_file("x", kind="memory").char_cap == 3300
    assert parse_memory_file("x", kind="user").char_cap == 3000


def test_char_count_matches_serialized_length() -> None:
    mf = parse_memory_file("First\n§\nSecond", kind="memory")
    assert mf.char_count == len(serialize_memory_file(mf))


def test_roundtrip_is_stable_for_clean_input() -> None:
    text = "First fact\n§\nSecond fact"
    mf = parse_memory_file(text, kind="memory")
    assert serialize_memory_file(mf) == text


def test_fill_ratio_is_count_over_cap() -> None:
    mf = parse_memory_file("x" * 2850, kind="user")  # cap 3000
    assert 0.9 < mf.fill_ratio < 1.0


# --------------------------------------------------------------------------- #
# Autonomous lossless compaction
# --------------------------------------------------------------------------- #
def test_curate_dedupes_exact_duplicates_keeping_first() -> None:
    mf = parse_memory_file("Same fact\n§\nOther\n§\nSame fact", kind="memory")
    plan = curate(mf)
    assert [e.text for e in plan.compacted.entries] == ["Same fact", "Other"]
    assert plan.compacted_chars < plan.original_chars


def test_curate_dedupe_ignores_whitespace_and_case_differences() -> None:
    mf = parse_memory_file("Docker Config\n§\n docker config ", kind="memory")
    plan = curate(mf)
    assert len(plan.compacted.entries) == 1


def test_curate_is_lossless_no_information_dropped() -> None:
    mf = parse_memory_file("A unique\n§\nB unique\n§\nC unique", kind="memory")
    plan = curate(mf)
    assert {e.text for e in plan.compacted.entries} == {"A unique", "B unique", "C unique"}


# --------------------------------------------------------------------------- #
# Owner-gated classification (candidates only, never applied)
# --------------------------------------------------------------------------- #
def test_curate_flags_durable_judgment_as_promotion_candidate() -> None:
    mf = parse_memory_file(
        "이름은 홍길동\n§\n앞으로 호의를 베푼 사람에게 은혜를 갚는 것을 원칙으로 한다",
        kind="user",
    )
    plan = curate(mf)
    cands = [e.text for e in plan.promotion_candidates]
    assert any("원칙" in t for t in cands)
    assert not any("이름은" in t for t in cands)


def test_curate_does_not_flag_short_identity_facts() -> None:
    mf = parse_memory_file("이름은 <owner-name>\n§\n호칭은 박사님\n§\n언어는 한국어", kind="user")
    plan = curate(mf)
    assert plan.promotion_candidates == ()


def test_promotion_candidates_remain_in_compacted_file() -> None:
    # curate NEVER removes a durable entry on its own; it only flags it.
    mf = parse_memory_file("앞으로 X는 이렇게 하는 것을 원칙으로 한다", kind="user")
    plan = curate(mf)
    assert any("원칙" in e.text for e in plan.compacted.entries)


# --------------------------------------------------------------------------- #
# Near-cap detection
# --------------------------------------------------------------------------- #
def test_near_cap_true_when_over_threshold() -> None:
    plan = curate(parse_memory_file("x" * 2850, kind="user"))  # ~95%
    assert plan.near_cap is True


def test_near_cap_false_when_well_below() -> None:
    plan = curate(parse_memory_file("short fact", kind="memory"))
    assert plan.near_cap is False


def test_plan_reports_headroom_chars() -> None:
    mf = parse_memory_file("x" * 1000, kind="user")  # cap 3000
    plan = curate(mf)
    assert plan.char_cap == 3000
    assert plan.headroom_after == 3000 - plan.compacted_chars


# --------------------------------------------------------------------------- #
# Types are frozen value objects
# --------------------------------------------------------------------------- #
def test_value_objects_are_frozen() -> None:
    import dataclasses

    for cls in (MemoryEntry, MemoryFile, CurationPlan):
        assert dataclasses.is_dataclass(cls)
        params = getattr(cls, "__dataclass_params__")
        assert params.frozen is True

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from automation.memory_curator.classify_model import (
    ROUTES,
    EntryVerdict,
    Route,
    VetoReason,
)


def test_routes_match_the_route_literal() -> None:
    # Given the public Route alias / When its Literal values are inspected
    literal_routes = frozenset(get_args(Route))

    # Then ROUTES is exact and cannot drift from the alias.
    assert ROUTES == {
        "TWIN", "OPS_REFERENCE", "PERSON_REFERENCE", "AGENT_RULE", "KEEP_NATIVE", "UNCERTAIN",
    }
    assert literal_routes == ROUTES


def test_veto_reasons_match_the_frozen_contract() -> None:
    # Given the public VetoReason alias / When its Literal values are inspected
    veto_reasons = frozenset(get_args(VetoReason))

    # Then all and only the seven contract values are present.
    assert veto_reasons == {
        "credential",
        "keep_native_rule",
        "marker",
        "too_short",
        "user_file",
        "parse",
        "llm_error",
    }


def test_entry_verdict_constructs_with_the_exact_fields() -> None:
    # Given valid contract values / When an entry verdict is constructed
    verdict = EntryVerdict(
        source_kind="memory",
        entry_text="Prefer deterministic routing.",
        route="TWIN",
        evidence="deterministic routing",
        reason="durable preference",
        veto=None,
        llm_called=True,
    )

    # Then every value is retained without transformation.
    assert verdict.source_kind == "memory"
    assert verdict.entry_text == "Prefer deterministic routing."
    assert verdict.route == "TWIN"
    assert verdict.evidence == "deterministic routing"
    assert verdict.reason == "durable preference"
    assert verdict.veto is None
    assert verdict.llm_called is True


def test_entry_verdict_is_frozen_and_slotted() -> None:
    # Given a constructed verdict / When ordinary mutation is attempted
    verdict = EntryVerdict(
        source_kind="user",
        entry_text="Uses concise answers.",
        route="KEEP_NATIVE",
        evidence="Uses concise answers.",
        reason="native user preference",
        veto="user_file",
        llm_called=False,
    )

    # Then frozen mutation fails and slots prevent an instance dictionary.
    with pytest.raises(FrozenInstanceError):
        setattr(verdict, "route", "TWIN")
    assert hasattr(EntryVerdict, "__slots__")
    assert not hasattr(verdict, "__dict__")

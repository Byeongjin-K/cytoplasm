from __future__ import annotations

import json
from collections.abc import Callable
from typing import Final, TypeAlias

from .classify_model import ROUTES, EntryVerdict
from .model import MemoryKind

JsonValue: TypeAlias = (
    "str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]"
)

_EXPECTED_KEYS: Final = frozenset({"route", "evidence", "reason"})
_FENCE_OPENERS: Final = frozenset({"```", "```json"})
_JSON_LOADS: Callable[[str], JsonValue] = json.loads


def _closed_verdict(source_kind: MemoryKind, entry_text: str) -> EntryVerdict:
    return EntryVerdict(
        source_kind=source_kind,
        entry_text=entry_text,
        route="UNCERTAIN",
        evidence="",
        reason="",
        veto="parse",
        llm_called=True,
    )


def _collapse(value: str) -> str:
    return " ".join(value.split())


def parse_verdict(
    raw: str,
    entry_text: str,
    *,
    source_kind: MemoryKind,
) -> EntryVerdict:
    candidate = raw.strip()
    lines = candidate.splitlines()
    if len(lines) >= 2 and lines[0] in _FENCE_OPENERS and lines[-1] == "```":
        candidate = "\n".join(lines[1:-1])

    try:
        payload = _JSON_LOADS(candidate)
    except (json.JSONDecodeError, RecursionError):
        return _closed_verdict(source_kind, entry_text)

    match payload:
        case dict() as fields if frozenset(fields) == _EXPECTED_KEYS:
            match (fields["route"], fields["evidence"], fields["reason"]):
                case (str() as route, str() as evidence, str() as reason):
                    if route not in ROUTES:
                        return _closed_verdict(source_kind, entry_text)
                    collapsed_evidence = _collapse(evidence)
                    if (
                        len(collapsed_evidence) < 8
                        or collapsed_evidence not in _collapse(entry_text)
                    ):
                        return _closed_verdict(source_kind, entry_text)
                    match route:
                        case (
                            "TWIN" | "OPS_REFERENCE" | "PERSON_REFERENCE" | "AGENT_RULE"
                            | "KEEP_NATIVE" | "UNCERTAIN"
                        ):
                            return EntryVerdict(
                                source_kind=source_kind,
                                entry_text=entry_text,
                                route=route,
                                evidence=evidence,
                                reason=reason[:200],
                                veto=None,
                                llm_called=True,
                            )
                        case _:
                            return _closed_verdict(source_kind, entry_text)
                case _:
                    return _closed_verdict(source_kind, entry_text)
        case _:
            return _closed_verdict(source_kind, entry_text)

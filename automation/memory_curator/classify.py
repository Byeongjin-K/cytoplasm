from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Final

from automation.twin_distill.llm import (
    LlmClient,
    LlmConfigurationError,
    LlmInvocationError,
)

from .classify_model import MOVABLE_ROUTES, EntryVerdict
from .classify_parse import parse_verdict
from .classify_prompt import render
from .classify_veto import post_llm_veto, pre_llm_veto
from .model import MemoryEntry, MemoryKind

_KIND_ORDER: Final[tuple[MemoryKind, ...]] = ("memory", "user")


def classify_entries(
    entries_by_kind: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    *,
    client: LlmClient,
) -> tuple[EntryVerdict, ...]:
    verdicts: list[EntryVerdict] = []
    for kind in _KIND_ORDER:
        for entry in entries_by_kind.get(kind, ()):
            verdict = pre_llm_veto(entry.text, source_kind=kind)
            if verdict is not None and verdict.veto != "keep_native_rule":
                verdicts.append(verdict)
                continue

            try:
                raw = client.complete(render(entry.text, source_kind=kind))
            except (LlmInvocationError, LlmConfigurationError):
                if verdict is not None:
                    verdicts.append(replace(verdict, llm_called=True))
                    continue
                verdicts.append(
                    EntryVerdict(
                        source_kind=kind,
                        entry_text=entry.text,
                        route="UNCERTAIN",
                        evidence="",
                        reason="",
                        veto="llm_error",
                        llm_called=True,
                    )
                )
                continue

            judged = post_llm_veto(parse_verdict(raw, entry.text, source_kind=kind))
            if verdict is not None and judged.route not in MOVABLE_ROUTES:
                judged = replace(verdict, llm_called=True)
            verdicts.append(judged)
    return tuple(verdicts)

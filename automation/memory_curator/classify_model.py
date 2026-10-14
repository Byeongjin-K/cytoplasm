from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from .model import MemoryKind

Route = Literal["TWIN", "OPS_REFERENCE", "PERSON_REFERENCE", "AGENT_RULE", "KEEP_NATIVE", "UNCERTAIN"]
VetoReason = Literal[
    "credential",
    "keep_native_rule",
    "marker",
    "too_short",
    "user_file",
    "parse",
    "llm_error",
]

#: All valid routes, derived from the Route Literal so it can never drift.
ROUTES: Final[frozenset[str]] = frozenset(
    {"TWIN", "OPS_REFERENCE", "PERSON_REFERENCE", "AGENT_RULE", "KEEP_NATIVE", "UNCERTAIN"}
)

#: Routes that move an entry even when it carries a keep-native cue: a colleague's 호칭 or
#: a mail-handling rule says "선임"/"문체"/"할 일" without being the owner's identity, so the
#: cue veto kept them in USER.md forever (owner decision Q138 ②, 2026-10-14).
MOVABLE_ROUTES: Final[frozenset[str]] = frozenset({"PERSON_REFERENCE", "AGENT_RULE"})


@dataclass(frozen=True, slots=True)
class EntryVerdict:
    source_kind: MemoryKind
    entry_text: str
    route: Route
    evidence: str
    reason: str
    veto: VetoReason | None
    llm_called: bool

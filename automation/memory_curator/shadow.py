from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, TypeAlias

from .binding import entry_digest
from .classify_model import EntryVerdict, Route, VetoReason
from .model import MemoryFile, MemoryKind
from .reclaim import reclaimable_chars
from .reporting import preview

SHADOW_SCHEMA: Final = "memory-curator-shadow-v1"

_KINDS: Final[tuple[MemoryKind, ...]] = ("memory", "user")
_ROUTES: Final[tuple[Route, ...]] = (
    "TWIN", "OPS_REFERENCE", "PERSON_REFERENCE", "AGENT_RULE", "KEEP_NATIVE", "UNCERTAIN",
)
_VETO_REASONS: Final[tuple[VetoReason, ...]] = (
    "credential",
    "keep_native_rule",
    "marker",
    "too_short",
    "user_file",
    "parse",
    "llm_error",
)

_Candidate: TypeAlias = tuple[int, MemoryKind, str, int, dict[str, object]]


def build_shadow_report(
    files: Mapping[MemoryKind, MemoryFile],
    verdicts: Sequence[EntryVerdict],
    *,
    cue_matched: Mapping[MemoryKind, frozenset[str]],
    full: bool,
) -> dict[str, object]:
    routes: dict[Route, dict[str, int]] = {
        route: {"count": 0, "reclaimable_chars": 0} for route in _ROUTES
    }
    vetoes: dict[VetoReason, int] = {reason: 0 for reason in _VETO_REASONS}
    cue_recall = {"cue_only": 0, "classifier_only": 0, "both": 0}
    claimed_indices: dict[MemoryKind, set[int]] = {kind: set() for kind in _KINDS}
    candidates: list[_Candidate] = []
    llm_calls = 0

    for verdict in verdicts:
        memory_file = files[verdict.source_kind]
        claimed = claimed_indices[verdict.source_kind]
        index = next(
            (
                entry_index
                for entry_index, entry in enumerate(memory_file.entries)
                if entry.text == verdict.entry_text and entry_index not in claimed
            ),
            None,
        )
        if index is None:
            message = f"verdict entry is not present in the {verdict.source_kind} memory file"
            raise ValueError(message)
        claimed.add(index)

        routes[verdict.route]["count"] += 1
        if verdict.veto is not None:
            vetoes[verdict.veto] += 1
        if verdict.llm_called:
            llm_calls += 1

        matched_by_cue = verdict.entry_text in cue_matched.get(verdict.source_kind, frozenset())
        match verdict.route:
            case "TWIN":
                cue_recall["both" if matched_by_cue else "classifier_only"] += 1
            case "OPS_REFERENCE" | "PERSON_REFERENCE" | "AGENT_RULE" | "KEEP_NATIVE" | "UNCERTAIN":
                if matched_by_cue:
                    cue_recall["cue_only"] += 1

        match verdict.route:
            case "TWIN" | "OPS_REFERENCE" | "PERSON_REFERENCE" | "AGENT_RULE":
                reclaimable = reclaimable_chars(memory_file, index)
                routes[verdict.route]["reclaimable_chars"] += reclaimable
                digest = entry_digest(verdict.source_kind, verdict.entry_text)
                rendered_preview = " ".join(verdict.entry_text.split()) if full else preview(verdict.entry_text)
                candidates.append(
                    (
                        reclaimable,
                        verdict.source_kind,
                        digest,
                        index,
                        {
                            "kind": verdict.source_kind,
                            "route": verdict.route,
                            "reclaimable_chars": reclaimable,
                            "digest8": digest[:8],
                            "preview": rendered_preview,
                            "evidence": verdict.evidence,
                            "reason": verdict.reason,
                            "cue_matched": matched_by_cue,
                        },
                    )
                )
            case "KEEP_NATIVE" | "UNCERTAIN":
                pass

    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1], candidate[2], candidate[3]))
    twin_chars = routes["TWIN"]["reclaimable_chars"]
    ops_chars = routes["OPS_REFERENCE"]["reclaimable_chars"]
    memory_reclaimable_total = sum(
        candidate[0] for candidate in candidates if candidate[1] == "memory"
    )
    user_reclaimable_total = sum(candidate[0] for candidate in candidates if candidate[1] == "user")
    memory_file = files["memory"]
    user_file = files["user"]

    return {
        "schema": SHADOW_SCHEMA,
        "files": [
            {
                "kind": kind,
                "entries": len(files[kind].entries),
                "chars": files[kind].char_count,
                "cap": files[kind].char_cap,
                "fill_pct": round(files[kind].char_count / files[kind].char_cap * 100, 1),
            }
            for kind in _KINDS
        ],
        "routes": routes,
        "vetoes": vetoes,
        "llm_calls": llm_calls,
        "cue_recall": cue_recall,
        "candidates": [candidate[4] for candidate in candidates],
        "projected": {
            "twin_chars": twin_chars,
            "ops_chars": ops_chars,
            "total_chars": twin_chars + ops_chars,
            "memory_fill_pct_after": round(
                (memory_file.char_count - memory_reclaimable_total) / memory_file.char_cap * 100,
                1,
            ),
            "user_fill_pct_after": round(
                (user_file.char_count - user_reclaimable_total) / user_file.char_cap * 100,
                1,
            ),
        },
        "side_effects": {
            "files_written": 0,
            "discord_posts": 0,
            "state_writes": 0,
            "obsidian_writes": 0,
        },
    }

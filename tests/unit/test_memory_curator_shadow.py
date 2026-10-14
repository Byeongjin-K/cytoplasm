from __future__ import annotations

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.model import MemoryEntry, MemoryFile, MemoryKind
from automation.memory_curator.shadow import SHADOW_SCHEMA, build_shadow_report


def _memory_file(kind: MemoryKind, *texts: str) -> MemoryFile:
    return MemoryFile(kind, tuple(MemoryEntry(text) for text in texts))


def _inputs() -> tuple[
    dict[MemoryKind, MemoryFile],
    tuple[EntryVerdict, ...],
    dict[MemoryKind, frozenset[str]],
]:
    classifier_only = "classifier durable rule"
    both = "rule\tABCDEFGHIJKLMNOPQRSTU"
    keep_native = "keep native preference"
    uncertain = "needs owner decision"
    cue_only = "legacy operations reference"
    files: dict[MemoryKind, MemoryFile] = {
        "memory": _memory_file("memory", classifier_only, both, keep_native, uncertain),
        "user": _memory_file("user", cue_only),
    }
    verdicts = (
        EntryVerdict("memory", classifier_only, "TWIN", "classifier", "durable", None, True),
        EntryVerdict("memory", both, "TWIN", "classifier", "durable", None, True),
        EntryVerdict("user", cue_only, "OPS_REFERENCE", "operator", "reference", "credential", False),
        EntryVerdict(
            "memory",
            keep_native,
            "KEEP_NATIVE",
            "rule",
            "native",
            "keep_native_rule",
            False,
        ),
        EntryVerdict("memory", uncertain, "UNCERTAIN", "parse", "unclear", "parse", True),
    )
    cue_matched: dict[MemoryKind, frozenset[str]] = {
        "memory": frozenset({both}),
        "user": frozenset({cue_only}),
    }
    return files, verdicts, cue_matched


def test_build_shadow_report_summarizes_routes_candidates_and_projection() -> None:
    # Given: classifier verdicts that distinguish cue-only, classifier-only, and overlapping hits
    files, verdicts, cue_matched = _inputs()

    # When: the curator builds its default privacy-bounded diagnostic report
    report = build_shadow_report(files, verdicts, cue_matched=cue_matched, full=False)

    # Then: the frozen report shape accounts for every route and veto without mutation
    assert set(report) == {
        "schema",
        "files",
        "routes",
        "vetoes",
        "llm_calls",
        "cue_recall",
        "candidates",
        "projected",
        "side_effects",
    }
    assert report["schema"] == SHADOW_SCHEMA
    assert report["files"] == [
        {"kind": "memory", "entries": 4, "chars": 100, "cap": 3300, "fill_pct": 3.0},
        {"kind": "user", "entries": 1, "chars": 27, "cap": 3000, "fill_pct": 0.9},
    ]
    assert report["routes"] == {
        "TWIN": {"count": 2, "reclaimable_chars": 55},
        "OPS_REFERENCE": {"count": 1, "reclaimable_chars": 27},
        "KEEP_NATIVE": {"count": 1, "reclaimable_chars": 0},
        "UNCERTAIN": {"count": 1, "reclaimable_chars": 0},
    }
    assert report["vetoes"] == {
        "credential": 1,
        "keep_native_rule": 1,
        "marker": 0,
        "too_short": 0,
        "user_file": 0,
        "parse": 1,
        "llm_error": 0,
    }
    assert report["llm_calls"] == 3
    assert report["cue_recall"] == {"cue_only": 1, "classifier_only": 1, "both": 1}
    assert report["candidates"] == [
        {
            "kind": "memory",
            "route": "TWIN",
            "reclaimable_chars": 29,
            "digest8": entry_digest("memory", "rule\tABCDEFGHIJKLMNOPQRSTU")[:8],
            "preview": "rule [REDACTED]",
            "evidence": "classifier",
            "reason": "durable",
            "cue_matched": True,
        },
        {
            "kind": "user",
            "route": "OPS_REFERENCE",
            "reclaimable_chars": 27,
            "digest8": entry_digest("user", "legacy operations reference")[:8],
            "preview": "legacy operations reference",
            "evidence": "operator",
            "reason": "reference",
            "cue_matched": True,
        },
        {
            "kind": "memory",
            "route": "TWIN",
            "reclaimable_chars": 26,
            "digest8": entry_digest("memory", "classifier durable rule")[:8],
            "preview": "classifier durable rule",
            "evidence": "classifier",
            "reason": "durable",
            "cue_matched": False,
        },
    ]
    assert report["projected"] == {
        "twin_chars": 55,
        "ops_chars": 27,
        "total_chars": 82,
        "memory_fill_pct_after": 1.4,
        "user_fill_pct_after": 0.0,
    }
    projected = report["projected"]
    assert isinstance(projected, dict)
    assert projected["total_chars"] == projected["twin_chars"] + projected["ops_chars"]
    assert report["side_effects"] == {
        "files_written": 0,
        "discord_posts": 0,
        "state_writes": 0,
        "obsidian_writes": 0,
    }


def test_build_shadow_report_is_pure_deterministic_and_can_show_full_text() -> None:
    # Given: only immutable in-memory values, including a token-shaped entry
    files, verdicts, cue_matched = _inputs()

    # When: the same diagnostic is built twice, then once with full review enabled
    first = build_shadow_report(files, verdicts, cue_matched=cue_matched, full=False)
    second = build_shadow_report(files, verdicts, cue_matched=cue_matched, full=False)
    full = build_shadow_report(files, verdicts, cue_matched=cue_matched, full=True)

    # Then: no I/O fixture is needed because the function receives no I/O handle,
    # and equal inputs yield equal reports while full mode exposes collapsed text.
    assert first == second
    candidates = full["candidates"]
    assert isinstance(candidates, list)
    assert candidates[0]["preview"] == "rule ABCDEFGHIJKLMNOPQRSTU"

"""Whole-store candidates, one passage per note, and one model pick (owner Q155, 2026-10-15).

The embedding search already holds the needed note for most owner requests, but ranks it
below look-alike passages; a single model call over the first 100 distinct notes picks the
ones that state the needed fact. Settings were fixed on evaluation set A and judged once on
sealed set D. The caller supplies the model call, so this module never names a model.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from automation.knowledge.pack import EvidencePack, KnowledgeQuery
from automation.knowledge.rank import item_from_rag

CANDIDATE_NOTES: Final = 100
EXCERPT_CHARS: Final = 240
MAX_PICKS: Final = 4
PROMPT_PATH: Final = Path(__file__).resolve().parents[2] / "prompts" / "memory-pick-v1.md"
_OBJECT: Final = re.compile(r"\{.*\}", re.S)
_CANDIDATE_ID: Final = re.compile(r"c(\d{3})")

Complete = Callable[[str], str]
Rows = list[dict[str, Any]]


def build_prompt(message: str, rows: Rows) -> str:
    lines = [
        f"[c{index:03d}] {str(row.get('source', ''))[:80]}\n"
        + re.sub(r"\s+", " ", str(row.get("content", "")))[:EXCERPT_CHARS]
        for index, row in enumerate(rows, 1)
    ]
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{MESSAGE}", message).replace("{CANDIDATES}", "\n\n".join(lines))


def parse_pick(answer: str, count: int) -> tuple[int, ...] | None:
    """Candidate positions in pick order; None when the answer is not the asked JSON."""
    match = _OBJECT.search(answer)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    ids = data.get("ids") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        return None
    picked: list[int] = []
    for raw in ids:
        found = _CANDIDATE_ID.fullmatch(raw) if isinstance(raw, str) else None
        position = int(found.group(1)) - 1 if found else -1
        if 0 <= position < count and position not in picked:
            picked.append(position)
        if len(picked) == MAX_PICKS:
            break
    return tuple(picked)


def choose(text: str, rows: Rows, complete: Complete) -> Rows | None:
    """The picked rows in pick order; None when the model's answer is not the asked JSON."""
    if not rows:
        return []
    picked = parse_pick(complete(build_prompt(text, rows)), len(rows))
    return None if picked is None else [rows[position] for position in picked]


def pick_pack(query: KnowledgeQuery, rows: Rows, complete: Complete) -> EvidencePack:
    layers = {"rag": "no_memory", "wiki": "skipped", "twin": "skipped"}
    chosen = choose(query.text, rows, complete)
    if chosen is None:
        return EvidencePack("knowledge-v1", query, "no_evidence", (), layers, ("pick 답 형식 불일치",))
    items = tuple(item_from_rag(row, True) for row in chosen)
    layers["rag"] = "hit" if items else "no_memory"
    return EvidencePack("knowledge-v1", query, "hit" if items else "no_evidence", items, layers)


def pick_evidence(
    query: KnowledgeQuery, complete: Complete, *, env: Mapping[str, str] = os.environ,
) -> EvidencePack:
    """Search the whole store once and let the caller's model pick; read-only."""
    from automation.knowledge.adapters.rag import memory_client
    from automation.rag_ingest.mcp_client import McpFatalError, McpUnreachableError

    try:
        rows = [dict(row) for row in memory_client(env).search_memory(
            query.text, limit=CANDIDATE_NOTES, one_per_note=True,
        )]
    except McpFatalError:
        from automation.knowledge.facade import collect_evidence

        return collect_evidence(query, env=env)
    except (OSError, ValueError, McpUnreachableError) as error:
        layers = {"rag": "unavailable", "wiki": "skipped", "twin": "skipped"}
        return EvidencePack("knowledge-v1", query, "unavailable", (), layers,
                            (f"rag 계층 불가({error.__class__.__name__})",))
    return pick_pack(query, rows, complete)

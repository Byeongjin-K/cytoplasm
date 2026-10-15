"""Whole-store recall pick: up to three searches, a shortlist, then a full-text confirm.

Owner Q170 (2026-10-15): "다 보류하고 고르는 판단 한 번 더(새 세트 H로)". Each search keeps the
first 60 new notes with their two best passages. The model shortlists from 300-character windows
cut where the owner's words (and its own search lines) are rarest among the candidates, then
reads the shortlisted notes in full and keeps the ones that state the needed fact. Tuned on
evaluation set T only, judged once on sealed set H (found 59/69, wrong-only 3/69, unrelated empty
45/45). The caller supplies the model call and the search, so this module never names a model.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from automation.knowledge.pack import EvidencePack, KnowledgeQuery
from automation.knowledge.rank import item_from_rag

CANDIDATE_NOTES: Final = 60
PASSAGES_PER_NOTE: Final = 2
WINDOW_CHARS: Final = 300
MAX_SEARCHES: Final = 3
SHORTLIST: Final = 8
MAX_PICKS: Final = 4
ATTACH_CHARS: Final = 4000
_PROMPTS: Final = Path(__file__).resolve().parents[2] / "prompts"
SHORTLIST_PROMPT: Final = _PROMPTS / "memory-pick-v2-shortlist.md"
CONFIRM_PROMPT: Final = _PROMPTS / "memory-confirm-v1.md"
_OBJECT: Final = re.compile(r"\{.*\}", re.S)
_CANDIDATE_ID: Final = re.compile(r"c(\d{3})")
_CHUNK_SUFFIX: Final = re.compile(r"#c\d+$")
# Obsidian info-callout and dateformat header lines carry no facts but filled the old 160-char prefix.
_BOILERPLATE: Final = re.compile(r"^\s*>\s*(\[!\w+\].*|\*\*(Author|Created|Modified)\*\*.*)?\s*$|=dateformat\(")
_TOKEN: Final = re.compile(r"[0-9a-z]+|[가-힣]+")
_WINDOW_STEP: Final = 10

Complete = Callable[[str], str]
Rows = list[dict[str, Any]]
Search = Callable[[str, Collection[str]], Rows]
Group = tuple[str, Rows]


def note_key(source: str) -> str:
    return _CHUNK_SUFFIX.sub("", source)


def group(rows: Rows, exclude: Collection[str], limit: int = CANDIDATE_NOTES) -> list[Group]:
    groups: dict[str, Rows] = {}
    for row in rows:
        key = note_key(str(row.get("source", "")))
        if key in exclude:
            continue
        if key not in groups:
            if len(groups) == limit:
                continue
            groups[key] = []
        groups[key].append(row)
    return list(groups.items())


def clean(text: str) -> str:
    kept = (line for line in str(text).splitlines() if not _BOILERPLATE.search(line))
    return re.sub(r"\s+", " ", "\n".join(kept)).strip()


def tokens(text: str) -> set[str]:
    out: set[str] = set()
    for word in _TOKEN.findall(text.lower()):
        if "가" <= word[0] <= "힣":
            out.update(word[index:index + 2] for index in range(len(word) - 1))
        elif len(word) >= 2:
            out.add(word)
    return out


def rarity(groups: Sequence[Group]) -> dict[str, float]:
    docs = [tokens(clean(row.get("content", ""))) for _key, rows in groups for row in rows[:PASSAGES_PER_NOTE]]
    total = len(docs)
    counts = Counter(word for doc in docs for word in doc)
    weights = {word: math.log(1 + total / (count + 0.5)) for word, count in counts.items()}
    weights[""] = math.log(1 + total / 0.5)
    return weights


def window(text: str, query_words: set[str], weights: Mapping[str, float], width: int = WINDOW_CHARS) -> str:
    body = clean(text)
    if len(body) <= width or not query_words:
        return body[:width]
    best_score, best_start = -1.0, 0
    for start in range(0, len(body) - width + _WINDOW_STEP, _WINDOW_STEP):
        score = sum(weights.get(word, weights[""]) for word in tokens(body[start:start + width]) & query_words)
        if score > best_score:
            best_score, best_start = score, start
    start = best_start if best_score > 0 else 0
    return ("…" if start else "") + body[start:start + width]


def shortlist_prompt(message: str, groups: Sequence[Group], queries: Sequence[str], searches_left: int) -> str:
    query_words = set().union(*(tokens(query) for query in queries)) if queries else set()
    weights = rarity(groups)
    blocks = [
        f"[c{index:03d}] {key[:80]}\n" + "\n…\n".join(
            window(str(row.get("content", "")), query_words, weights) for row in rows[:PASSAGES_PER_NOTE])
        for index, (key, rows) in enumerate(groups, 1)
    ]
    template = SHORTLIST_PROMPT.read_text(encoding="utf-8")
    return (template.replace("{LEFT}", str(searches_left)).replace("{MESSAGE}", message)
            .replace("{CANDIDATES}", "\n\n".join(blocks)))


def confirm_prompt(message: str, groups: Sequence[Group]) -> str:
    blocks = [
        f"[c{index:03d}] {key[:80]}\n" + "\n…\n".join(clean(row.get("content", "")) for row in rows[:PASSAGES_PER_NOTE])
        for index, (key, rows) in enumerate(groups, 1)
    ]
    template = CONFIRM_PROMPT.read_text(encoding="utf-8")
    return template.replace("{MESSAGE}", message).replace("{CANDIDATES}", "\n\n".join(blocks))


def parse_answer(answer: str, count: int, cap: int) -> tuple[str, Any]:
    """("search", line) | ("ids", positions in pick order) | ("bad", None)."""
    match = _OBJECT.search(answer or "")
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return ("bad", None)
    search = data.get("search")
    if isinstance(search, str) and search.strip():
        return ("search", search.strip()[:200])
    ids = data.get("ids")
    if not isinstance(ids, list):
        return ("bad", None)
    picked: list[int] = []
    for raw in ids:
        found = _CANDIDATE_ID.fullmatch(raw) if isinstance(raw, str) else None
        position = int(found.group(1)) - 1 if found else -1
        if 0 <= position < count and position not in picked:
            picked.append(position)
        if len(picked) == cap:
            break
    return ("ids", picked)


def choose(text: str, search: Search, complete: Complete, exclude: Collection[str] = frozenset()) -> list[Group]:
    groups = group(search(text, exclude), exclude)
    queries = [text]
    searches = 1
    while groups:
        kind, value = parse_answer(complete(shortlist_prompt(text, groups, queries, MAX_SEARCHES - searches)),
                                   len(groups), SHORTLIST)
        if kind == "search" and searches < MAX_SEARCHES:
            seen = {key for key, _rows in groups}
            more_exclude = {*exclude, *seen}
            groups = groups + group(search(value, more_exclude), more_exclude)
            queries.append(value)
            searches += 1
            continue
        shortlisted = [groups[position] for position in value] if kind == "ids" else []
        if not shortlisted:
            return []
        kind, value = parse_answer(complete(confirm_prompt(text, shortlisted)), len(shortlisted), MAX_PICKS)
        return [shortlisted[position] for position in value] if kind == "ids" else []
    return []


def attached_rows(chosen: Sequence[Group]) -> Rows:
    out: Rows = []
    used = 0
    for _key, rows in chosen:
        text = "\n…\n".join(str(row.get("content", "")) for row in rows[:PASSAGES_PER_NOTE])[:max(0, ATTACH_CHARS - used)]
        if not text:
            break
        used += len(text)
        out.append({**rows[0], "content": text})
    return out


def pick_pack(query: KnowledgeQuery, chosen: Sequence[Group]) -> EvidencePack:
    layers = {"rag": "no_memory", "wiki": "skipped", "twin": "skipped"}
    items = tuple(item_from_rag(row, True) for row in attached_rows(chosen))
    layers["rag"] = "hit" if items else "no_memory"
    return EvidencePack("knowledge-v1", query, "hit" if items else "no_evidence", items, layers)


def store_search(client: Any) -> Search:
    def search(text: str, exclude: Collection[str]) -> Rows:
        return [dict(row) for row in client.search_memory(
            text, limit=CANDIDATE_NOTES, one_per_note=True, per_note=PASSAGES_PER_NOTE,
            exclude_notes=sorted(exclude),
        )]
    return search


def pick_evidence(
    query: KnowledgeQuery, complete: Complete, *, env: Mapping[str, str] = os.environ,
    exclude: Collection[str] = frozenset(),
) -> EvidencePack:
    """Search the whole store and let the caller's model pick; read-only."""
    from automation.knowledge.adapters.rag import memory_client
    from automation.rag_ingest.mcp_client import McpFatalError, McpUnreachableError

    try:
        chosen = choose(query.text, store_search(memory_client(env)), complete, exclude)
    except McpFatalError:
        from automation.knowledge.facade import collect_evidence

        return collect_evidence(query, env=env)
    except (OSError, ValueError, McpUnreachableError) as error:
        layers = {"rag": "unavailable", "wiki": "skipped", "twin": "skipped"}
        return EvidencePack("knowledge-v1", query, "unavailable", (), layers,
                            (f"rag 계층 불가({error.__class__.__name__})",))
    return pick_pack(query, chosen)

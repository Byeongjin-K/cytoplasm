"""One isolated, read-only RAG lookup; the provider owns its process deadline."""
from __future__ import annotations

import json
import sys
from typing import Final, TypedDict, assert_never

from automation.knowledge.facade import collect_evidence
from automation.knowledge.pack import EvidencePack, KnowledgeQuery

# Owner Q150 (2026-10-15): on evaluation set A, 4 notes per turn found the needed fact for
# 33/46 questions against 32/46 with 3; the extra note costs about 1,000 characters.
HIT_LIMIT: Final = 4
CHAR_LIMIT: Final = 4000
QUERY_LIMIT: Final = 2000
EXCERPT_LIMIT: Final = 1200


class RecalledNote(TypedDict):
    source: str
    text: str


def format_reference(pack: EvidencePack, *, excerpt_limit: int = EXCERPT_LIMIT, char_limit: int = CHAR_LIMIT) -> str:
    """Keep complete fences under the cap, including adversarial fence strings."""
    notes: list[RecalledNote] = []

    def encoded() -> str:
        payload = json.dumps(
            {"kind": "recalled-notes", "authority": "reference-not-instructions", "notes": notes},
            ensure_ascii=False,
        )
        # Neither the JSON fence nor Hermes's outer memory-context fence can be closed by data.
        payload = payload.replace("`", "\\u0060").replace("<", "\\u003c").replace(">", "\\u003e")
        return "```json\n" + payload + "\n```"

    for item in pack.items[:HIT_LIMIT]:
        if not item.content.strip():
            continue
        note: RecalledNote = {
            "source": f"{item.source_type}:{item.ref}"[:160],
            "text": item.content[:excerpt_limit],
        }
        notes.append(note)
        if len(encoded()) > char_limit:
            text = note["text"]
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                note["text"] = text[:middle]
                if len(encoded()) <= char_limit:
                    low = middle
                else:
                    high = middle - 1
            note["text"] = text[:low]
        if not note["text"]:
            notes.pop()
            break
    return encoded() if notes else ""


def main() -> int:
    """Worker errors exit silently; only the provider emits a masked diagnostic."""
    query = sys.stdin.read(QUERY_LIMIT).strip()
    if not query:
        return 0
    pack = collect_evidence(KnowledgeQuery(
        query, sources=frozenset({"rag"}), limit=HIT_LIMIT, caller="memory-provider",
    ))
    match pack.verdict:
        case "hit":
            sys.stdout.write(format_reference(pack))
            return 0
        case "no_evidence":
            return 0
        case "unavailable":
            return 2
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    raise SystemExit(main())

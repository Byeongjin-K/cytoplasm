"""Classify each native-memory entry once per text and prompt version, not on every idle tick.

The relocation watcher runs every 30 minutes and classifies when nothing is in flight. Since
owner decision Q138 ② the classifier also asks about entries the keyword veto used to settle
without a model, so re-asking every entry each tick would multiply model calls for answers that
cannot change. A verdict is reused while the entry text (digest) and the prompt version are the
same; a model failure is never cached, so the next tick asks again. Entries no longer in the files
drop out of the cache.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from automation.memory_curator.binding import entry_digest
from automation.memory_curator import classify as _classify
from automation.memory_curator.classify_model import ROUTES, EntryVerdict
from automation.memory_curator.classify_prompt import MC_CLASSIFY_VERSION
from automation.memory_curator.model import MemoryEntry, MemoryKind
from automation.twin_distill.llm import LlmClient

_KIND_ORDER: Final[tuple[MemoryKind, ...]] = ("memory", "user")


def _key(kind: MemoryKind, text: str) -> str:
    return f"{MC_CLASSIFY_VERSION}:{entry_digest(kind, text)}"


def _load(path: Path) -> dict[str, dict[str, object]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in raw.items() if isinstance(value, dict)} if isinstance(raw, dict) else {}


def _verdict(kind: MemoryKind, text: str, cached: Mapping[str, object]) -> EntryVerdict | None:
    route, veto = cached.get("route"), cached.get("veto")
    if route not in ROUTES or not (veto is None or isinstance(veto, str)):
        return None
    return EntryVerdict(kind, text, route, str(cached.get("evidence", "")), str(cached.get("reason", "")), veto, False)  # type: ignore[arg-type]


def _save(path: Path, cache: Mapping[str, Mapping[str, object]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(name, 0o600)
    os.replace(name, path)


def classify_with_cache(
    entries_by_kind: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    *,
    client: LlmClient,
    path: Path,
) -> tuple[EntryVerdict, ...]:
    cache = _load(path)
    known: dict[str, EntryVerdict] = {}
    missing: dict[MemoryKind, list[MemoryEntry]] = {kind: [] for kind in _KIND_ORDER}
    for kind in _KIND_ORDER:
        for entry in entries_by_kind.get(kind, ()):
            hit = cache.get(_key(kind, entry.text))
            verdict = None if hit is None else _verdict(kind, entry.text, hit)
            if verdict is None:
                missing[kind].append(entry)
            else:
                known[_key(kind, entry.text)] = verdict
    fresh = _classify.classify_entries({kind: tuple(entries) for kind, entries in missing.items()}, client=client)
    for verdict in fresh:
        known.setdefault(_key(verdict.source_kind, verdict.entry_text), verdict)
    ordered = tuple(
        known[_key(kind, entry.text)] for kind in _KIND_ORDER for entry in entries_by_kind.get(kind, ())
    )
    _save(path, {
        _key(v.source_kind, v.entry_text): {"route": v.route, "evidence": v.evidence, "reason": v.reason, "veto": v.veto}
        for v in ordered
        if v.veto != "llm_error"
    })
    return ordered

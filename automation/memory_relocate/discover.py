"""Pick the next OPS_REFERENCE entry the node should propose for relocation.

Pure selection so the prod node drives reclamation itself instead of waiting for
someone to run the CLI.  Autonomy stops here: choosing a candidate is not an
external effect — the proposal still has to win cha's ✅ before anything is
written or deleted.

Conservative by construction: both native stores, never an entry a relocation
record already covers, and biggest-reclaim-first so each approval frees the most.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.model import MemoryFile, MemoryKind
from automation.memory_curator.reclaim import reclaimable_chars

_RELOCATABLE_KINDS: Final = frozenset[MemoryKind]({"memory", "user"})
_RELOCATABLE_ROUTE: Final = "OPS_REFERENCE"


@dataclass(frozen=True, slots=True)
class RelocationCandidate:
    source_kind: MemoryKind
    entry_text: str
    entry_sha256: str
    reclaimable_chars: int


def select_candidate(
    verdicts: Sequence[EntryVerdict],
    files: Mapping[str, MemoryFile],
    known_digests: frozenset[str],
) -> RelocationCandidate | None:
    """Return the biggest unhandled OPS_REFERENCE candidate, or None."""
    best: RelocationCandidate | None = None
    claimed: dict[MemoryKind, set[int]] = {kind: set() for kind in _RELOCATABLE_KINDS}
    for verdict in verdicts:
        if verdict.route != _RELOCATABLE_ROUTE or verdict.source_kind not in _RELOCATABLE_KINDS:
            continue
        memory_file = files.get(verdict.source_kind)
        if memory_file is None:
            continue
        digest = entry_digest(verdict.source_kind, verdict.entry_text)
        if digest in known_digests:
            continue
        claimed_indices = claimed[verdict.source_kind]
        index = _index_of(memory_file, verdict.entry_text, claimed_indices)
        if index is None:
            continue
        claimed_indices.add(index)
        candidate = RelocationCandidate(
            verdict.source_kind,
            verdict.entry_text,
            digest,
            reclaimable_chars(memory_file, index),
        )
        if best is None or _outranks(candidate, best):
            best = candidate
    return best


#: Route → destination of a batched move (owner Q138 ②). OPS_REFERENCE keeps its one-at-a-time path.
_BATCH_DESTINATIONS: Final[dict[str, str]] = {"PERSON_REFERENCE": "person", "AGENT_RULE": "memory"}


@dataclass(frozen=True, slots=True)
class BatchCandidate:
    candidate: RelocationCandidate
    destination: str


def select_batch(
    verdicts: Sequence[EntryVerdict],
    files: Mapping[str, MemoryFile],
    known_digests: frozenset[str],
) -> tuple[BatchCandidate, ...]:
    """Every unhandled person fact and USER.md handling rule, in file order (one card covers them)."""
    found: list[BatchCandidate] = []
    claimed: dict[MemoryKind, set[int]] = {kind: set() for kind in _RELOCATABLE_KINDS}
    for verdict in verdicts:
        destination = _BATCH_DESTINATIONS.get(verdict.route)
        if destination is None or verdict.source_kind not in _RELOCATABLE_KINDS:
            continue
        if destination == "memory" and verdict.source_kind != "user":
            continue
        memory_file = files.get(verdict.source_kind)
        digest = entry_digest(verdict.source_kind, verdict.entry_text)
        if memory_file is None or digest in known_digests:
            continue
        index = _index_of(memory_file, verdict.entry_text, claimed[verdict.source_kind])
        if index is None:
            continue
        claimed[verdict.source_kind].add(index)
        found.append(BatchCandidate(
            RelocationCandidate(verdict.source_kind, verdict.entry_text, digest, reclaimable_chars(memory_file, index)),
            destination,
        ))
    return tuple(found)


def _index_of(memory_file: MemoryFile, text: str, claimed: set[int]) -> int | None:
    for index, entry in enumerate(memory_file.entries):
        if entry.text == text and index not in claimed:
            return index
    return None


def _outranks(candidate: RelocationCandidate, best: RelocationCandidate) -> bool:
    """Biggest reclaim wins; ties break on the digest so the choice is deterministic."""
    return (candidate.reclaimable_chars, best.entry_sha256) > (
        best.reclaimable_chars,
        candidate.entry_sha256,
    )

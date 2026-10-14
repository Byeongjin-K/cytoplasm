from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

RELOCATION_HASH_VERSION = "mc-reloc-v1"
BATCH_HASH_VERSION = "mc-reloc-batch-v1"
_DESTINATION_KINDS = {None: "obsidian", "person": "obsidian-person", "memory": "native-memory"}


@dataclass(frozen=True, slots=True)
class RelocationHashFields:
    source_kind: str
    entry_sha256: str
    note_relpath: str
    note_plan_sha256: str
    destination: str | None = None


def relocation_action_hash(fields: RelocationHashFields) -> str:
    encoded = json.dumps(
        {
            "delete_intent": True,
            "destination_kind": _DESTINATION_KINDS[fields.destination],
            "note_plan_sha256": fields.note_plan_sha256,
            "note_relpath": fields.note_relpath,
            "source_entry_sha256": fields.entry_sha256,
            "source_kind": fields.source_kind,
            "version": RELOCATION_HASH_VERSION,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def batch_action_hash(member_action_hashes: list[str]) -> str:
    """One ✅ authorizes exactly this set of per-entry hashes, in any order."""
    encoded = json.dumps(
        {"members": sorted(member_action_hashes), "version": BATCH_HASH_VERSION},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

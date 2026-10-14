from __future__ import annotations

import hashlib
import json
import re
from dataclasses import fields, replace

from automation.memory_relocate.binding import (
    RELOCATION_HASH_VERSION,
    RelocationHashFields,
    relocation_action_hash,
)


def _fields() -> RelocationHashFields:
    return RelocationHashFields(
        source_kind="memory",
        entry_sha256="entry-digest",
        note_relpath="Areas/research-memory.md",
        note_plan_sha256="note-plan-digest",
    )


def test_relocation_action_hash_is_deterministic_and_has_sha256_shape() -> None:
    first_fields = _fields()
    second_fields = RelocationHashFields(
        note_plan_sha256="note-plan-digest",
        note_relpath="Areas/research-memory.md",
        entry_sha256="entry-digest",
        source_kind="memory",
    )

    first = relocation_action_hash(first_fields)
    second = relocation_action_hash(second_fields)

    assert first == second
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", first)


def test_relocation_action_hash_matches_sorted_composite_preimage() -> None:
    canonical_items = (
        ("delete_intent", True),
        ("destination_kind", "obsidian"),
        ("note_plan_sha256", "note-plan-digest"),
        ("note_relpath", "Areas/research-memory.md"),
        ("source_entry_sha256", "entry-digest"),
        ("source_kind", "memory"),
        ("version", "mc-reloc-v1"),
    )
    forward_payload = dict(canonical_items)
    reverse_payload = dict(reversed(canonical_items))

    forward_preimage = json.dumps(
        forward_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    reverse_preimage = json.dumps(
        reverse_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert forward_preimage == reverse_preimage
    assert b'"delete_intent":true' in forward_preimage
    assert b'"destination_kind":"obsidian"' in forward_preimage
    assert b'"version":"mc-reloc-v1"' in forward_preimage
    assert RELOCATION_HASH_VERSION == "mc-reloc-v1"
    assert relocation_action_hash(_fields()) == (
        f"sha256:{hashlib.sha256(forward_preimage).hexdigest()}"
    )


def test_relocation_action_hash_excludes_rendered_note_dates() -> None:
    rendered_before_midnight = "Created: 2026-07-31\nModified: 2026-07-31\nBody"
    rendered_after_midnight = "Created: 2026-08-01\nModified: 2026-08-01\nBody"
    before_hash = hashlib.sha256(rendered_before_midnight.encode("utf-8")).hexdigest()
    after_hash = hashlib.sha256(rendered_after_midnight.encode("utf-8")).hexdigest()
    before_fields = _fields()
    after_fields = _fields()

    assert before_hash != after_hash
    assert tuple(field.name for field in fields(RelocationHashFields)) == (
        "source_kind",
        "entry_sha256",
        "note_relpath",
        "note_plan_sha256",
        "destination",
    )
    assert relocation_action_hash(before_fields) == relocation_action_hash(after_fields)


def test_relocation_action_hash_changes_with_each_authorizing_field() -> None:
    baseline_fields = _fields()
    baseline_hash = relocation_action_hash(baseline_fields)
    changed_fields = (
        replace(baseline_fields, source_kind="user"),
        replace(baseline_fields, entry_sha256="other-entry-digest"),
        replace(baseline_fields, note_relpath="Areas/other-memory.md"),
        replace(baseline_fields, note_plan_sha256="other-note-plan-digest"),
    )

    assert all(relocation_action_hash(item) != baseline_hash for item in changed_fields)

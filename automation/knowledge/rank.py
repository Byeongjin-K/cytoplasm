"""Evidence normalization, authority ordering, union, and deduplication."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any

from automation.knowledge.pack import DateBasis, EvidenceItem, Store

_PATH_DATE = re.compile(r"(20\d{2})(\d{2})(\d{2})")
_CHUNK = re.compile(r"#c\d+$")


def derive_doc_date(metadata: dict[str, Any], ref: str) -> tuple[str | None, DateBasis]:
    for key in ("event_date", "created", "document_updated", "updated", "day"):
        value = metadata.get(key)
        if isinstance(value, str) and re.match(r"^20\d{2}-\d{2}-\d{2}", value):
            if key == "created":
                return value[:10], "created"
            if key in {"document_updated", "updated"}:
                return value[:10], "updated"
            return value[:10], "day"
    match = _PATH_DATE.search(str(metadata.get("path", "")) or ref)
    if match:
        return "-".join(match.groups()), "path"
    return None, "none"


def item_from_rag(row: dict[str, Any], grounded: bool) -> EvidenceItem:
    raw_meta = row.get("metadata")
    metadata = raw_meta if isinstance(raw_meta, dict) else {}
    ref = str(metadata.get("path", "")) or _CHUNK.sub("", str(row.get("source", "")))
    source_type = str(metadata.get("source_type", "")) or str(row.get("source", "")).partition(":")[0]
    store: Store = "obsidian" if source_type == "obsidian" else "rag"
    doc_date, basis = derive_doc_date(metadata, ref)
    content = str(row.get("content", ""))
    return EvidenceItem("", store, source_type, ref, str(metadata.get("title", "")), doc_date, basis, float(row.get("score", 0.0)), grounded, None, None, content, "")


def item_from_wiki(note: dict[str, Any], *, twin: bool = False) -> EvidenceItem:
    meta_raw = note.get("meta")
    meta = meta_raw if isinstance(meta_raw, dict) else {}
    ref = str(note.get("slug", ""))
    doc_date, basis = derive_doc_date(
        {"event_date": meta.get("event_date"), "updated": meta.get("updated")}, ref
    )
    content = str(note.get("content", note.get("body", "")))
    authority = str(note.get("authority", meta.get("authority", ""))) or None
    expired_raw = note.get("expired")
    return EvidenceItem("", "wiki", "twin" if twin else "wiki", ref, str(meta.get("title", ref)), doc_date, basis, None, None, authority, bool(expired_raw) if expired_raw is not None else None, content, "")


def _authority(item: EvidenceItem) -> int:
    if item.store == "wiki" and not item.expired:
        return 0
    if item.store == "obsidian" or item.source_type == "note":
        return 1
    return 2


def _sort_key(item: EvidenceItem) -> tuple[int, int, str, float, int, str]:
    return (_authority(item), item.doc_date is None, "" if item.doc_date is None else _invert_date(item.doc_date), -(item.score or 0.0), 0 if item.source_type == "twin" else 1, item.ref)


def _invert_date(value: str) -> str:
    return "".join(chr(255 - ord(char)) for char in value)


def _canonical_ref(ref: str) -> str:
    value = _CHUNK.sub("", ref)
    if value.startswith(("wiki:", "obsidian:")):
        value = value.split(":", 1)[1]
    return value.removesuffix(".md")


def rank_and_deduplicate(items: list[EvidenceItem], *, limit: int) -> tuple[EvidenceItem, ...]:
    ranked = sorted(items, key=_sort_key)
    selected: list[EvidenceItem] = []
    hashes: set[str] = set()
    refs: set[str] = set()
    for item in ranked:
        digest = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
        canonical = _canonical_ref(item.ref)
        if digest in hashes or canonical in refs:
            continue
        hashes.add(digest)
        refs.add(canonical)
        selected.append(replace(item, id=f"E{len(selected) + 1}", sha256=digest))
        if len(selected) == limit:
            break
    return tuple(selected)

"""Offline source injection and KNOWLEDGE_FAKE_PACK loader."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, cast

from automation.knowledge.core import grounded_rows
from automation.knowledge.pack import DateBasis, EvidenceItem, EvidencePack, KnowledgeQuery, Purpose, Store, Verdict
from automation.knowledge.rank import item_from_rag, item_from_wiki


@dataclass(frozen=True, slots=True)
class FakeSources:
    rag_items: tuple[EvidenceItem, ...] = ()
    wiki_items: tuple[EvidenceItem, ...] = ()
    twin_items: tuple[EvidenceItem, ...] = ()
    rag_status: str = "no_memory"
    wiki_status: str = "none"
    twin_status: str = "none"
    notes: tuple[str, ...] = ()
    rag_error: Exception | None = field(default=None, compare=False)
    wiki_error: Exception | None = field(default=None, compare=False)
    twin_error: Exception | None = field(default=None, compare=False)

    @classmethod
    def from_fixture_dir(cls, root: Path) -> FakeSources:
        payload = json.loads((root / "rag_rows_hit.json").read_text(encoding="utf-8"))
        rag_items = tuple(item_from_rag(row, grounded) for row, grounded in grounded_rows("배양 실험", payload))
        wiki_items: list[EvidenceItem] = []
        twin_items: list[EvidenceItem] = []
        for path in sorted((root / "wiki_vault").glob("*.md")):
            meta, body = _parse_fixture_note(path.read_text(encoding="utf-8"))
            payload_note = {"slug": path.stem, "meta": meta, "body": body, "expired": False}
            wiki_items.append(item_from_wiki(payload_note))
            if meta.get("kind"):
                twin_items.append(item_from_wiki(payload_note, twin=True))
        return cls(rag_items, tuple(wiki_items), tuple(twin_items), "hit", "hit", "conflict", ())


def _parse_fixture_note(text: str) -> tuple[dict[str, Any], str]:
    _, header, body = text.split("---\n", 2)
    meta: dict[str, Any] = {}
    for line in header.splitlines():
        key, _, raw = line.partition(":")
        value = raw.strip().strip('"')
        if value.startswith("["):
            meta[key] = [part.strip().strip('"') for part in value[1:-1].split(",") if part.strip()]
        else:
            meta[key] = value
    return meta, body.strip()


def pack_to_dict(pack: EvidencePack) -> dict[str, Any]:
    return asdict(pack)


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"fake pack {field_name} malformed")
    return tuple(cast(list[str] | tuple[str, ...], value))


def _item(raw: object) -> EvidenceItem:
    if not isinstance(raw, Mapping):
        raise ValueError("fake pack item malformed")
    required = ("id", "store", "source_type", "ref", "title", "date_basis", "content", "sha256")
    if not all(isinstance(raw.get(key), str) for key in required):
        raise ValueError("fake pack item string field malformed")
    store = str(raw["store"])
    basis = str(raw["date_basis"])
    if store not in {"obsidian", "wiki", "rag"} or basis not in {"created", "updated", "day", "path", "none"}:
        raise ValueError("fake pack item enum malformed")
    score = raw.get("score")
    if score is not None and not isinstance(score, (int, float)):
        raise ValueError("fake pack item score malformed")
    optional_strings = tuple(raw.get(key) for key in ("doc_date", "authority"))
    if not all(value is None or isinstance(value, str) for value in optional_strings):
        raise ValueError("fake pack item optional string malformed")
    for key in ("grounded", "expired"):
        if raw.get(key) is not None and not isinstance(raw.get(key), bool):
            raise ValueError(f"fake pack item {key} malformed")
    return EvidenceItem(
        str(raw["id"]), cast(Store, store), str(raw["source_type"]), str(raw["ref"]),
        str(raw["title"]), cast(str | None, raw.get("doc_date")), cast(DateBasis, basis),
        float(score) if score is not None else None, cast(bool | None, raw.get("grounded")),
        cast(str | None, raw.get("authority")), cast(bool | None, raw.get("expired")),
        str(raw["content"]), str(raw["sha256"]),
    )


def pack_from_dict(raw: Mapping[str, object]) -> EvidencePack:
    query_raw = raw.get("query")
    if not isinstance(query_raw, Mapping):
        raise ValueError("fake pack query missing")
    purpose = str(query_raw.get("purpose", "cite"))
    if purpose not in {"cite", "synthesize", "entity", "judgment"}:
        raise ValueError("fake pack purpose malformed")
    limit = query_raw.get("limit", 8)
    if not isinstance(limit, int):
        raise ValueError("fake pack limit malformed")
    query = KnowledgeQuery(str(query_raw.get("text", "")), cast(Purpose, purpose), frozenset(_strings(query_raw.get("sources", ("rag", "wiki", "twin")), "sources")), frozenset(_strings(query_raw.get("tags", ()), "tags")), limit, str(query_raw.get("caller", "")))
    items_raw = raw.get("items", ())
    if not isinstance(items_raw, (list, tuple)):
        raise ValueError("fake pack items malformed")
    layers_raw = raw.get("layers", {})
    if not isinstance(layers_raw, Mapping):
        raise ValueError("fake pack layers malformed")
    verdict = str(raw.get("verdict", "unavailable"))
    if verdict not in {"hit", "no_evidence", "unavailable"}:
        raise ValueError("fake pack verdict malformed")
    notes = _strings(raw.get("notes", ()), "notes")
    return EvidencePack("knowledge-v1", query, cast(Verdict, verdict), tuple(_item(item) for item in items_raw), {str(key): str(value) for key, value in layers_raw.items()}, notes)


def load_fake_pack(env: Mapping[str, str]) -> EvidencePack | None:
    location = env.get("KNOWLEDGE_FAKE_PACK")
    if not location:
        return None
    path = Path(location).expanduser()
    text = path.read_text(encoding="utf-8") if path.is_file() else location
    raw = json.loads(text)
    if not isinstance(raw, Mapping):
        raise ValueError("KNOWLEDGE_FAKE_PACK must be a JSON object")
    return pack_from_dict(raw)

"""Private delta snapshots for proposal improvement."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, cast

from pytest import MonkeyPatch

from automation.knowledge.pack import DateBasis, EvidenceItem, EvidencePack, KnowledgeQuery

from skills.proposal.scripts.proposal_route_guard import assert_route_allowed

proposal_delta = importlib.import_module("skills.proposal.scripts.proposal_delta")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sources():
    return (
        proposal_delta.DeltaSource(
            "meeting", "note:meetings/planning.md", b"meeting decisions\n"
        ),
        proposal_delta.DeltaSource(
            "obsidian", "obsidian:Projects/proposal.md", b"owner note revision\n"
        ),
        proposal_delta.DeltaSource(
            "note", "note:personal/weekly.md", b"weekly note\n"
        ),
    )


class _Facade:
    KnowledgeQuery: ClassVar[type[KnowledgeQuery]] = KnowledgeQuery
    items: tuple[object, ...]

    def __init__(self, items: tuple[object, ...]) -> None:
        self.items = items

    def collect_evidence(self, query: KnowledgeQuery) -> EvidencePack:
        items = self.items if query.sources == frozenset({"rag"}) else ()
        return EvidencePack(
            "knowledge-v1",
            query,
            "hit" if items else "no_evidence",
            cast(tuple[EvidenceItem, ...], items),
            {"rag": "hit" if items else "no_memory", "wiki": "none", "twin": "none"},
        )


def _facade_item(
    ref: str,
    content: str,
    *,
    doc_date: str | None,
    date_basis: str,
    sha256: str,
    source_type: str = "note",
) -> EvidenceItem:
    return EvidenceItem(
        "E1", "rag", source_type, ref, ref, doc_date, cast(DateBasis, date_basis),
        0.9, True, None, None, content, sha256,
    )


def _prior_version(root: Path, old) -> None:
    delta = root / "demo" / "versions" / "v000001" / "delta"
    delta.mkdir(parents=True)
    (delta / "INDEX.json").write_text(
        json.dumps(
            [
                {
                    "source_key": old.source_key,
                    "sha256": _digest(old.content),
                    "collected_at": "2026-08-16T00:00:00Z",
                    "sections": [],
                }
            ]
        ),
        encoding="utf-8",
    )


def test_three_source_types_are_byte_identical_indexed_and_route_safe(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "proposals"
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))  # type: ignore[attr-defined]
    destination = root / "demo" / "staging" / ("1" * 64)
    sources = _sources()

    report = proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=destination, sources=sources
    )

    assert report.collected_count == 3
    assert report.counts == {"meeting": 1, "obsidian": 1, "note": 1}
    index_path = destination / "delta" / "INDEX.json"
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    assert_route_allowed(
        index_path.read_text(encoding="utf-8"),
        "drive",
        classification="owner-private",
        payload_kind="index",
    )
    assert {entry["source_key"] for entry in entries} == {
        source.source_key for source in sources
    }
    raw_files = sorted((destination / "delta" / "raw").iterdir())
    assert len(raw_files) == 3
    by_digest = {_digest(path.read_bytes()): path.read_bytes() for path in raw_files}
    assert by_digest == {_digest(source.content): source.content for source in sources}
    assert all(entry["sha256"] in by_digest for entry in entries)


def test_recollection_and_prior_version_hashes_are_skipped(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "proposals"
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))  # type: ignore[attr-defined]
    old = proposal_delta.DeltaSource(
        "wiki", "wiki:old-decision", b"already applied decision\n"
    )
    _prior_version(root, old)
    destination = root / "demo" / "staging" / ("2" * 64)
    offered = (*_sources(), old)

    first = proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=destination, sources=offered
    )
    second = proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=destination, sources=offered
    )

    assert first.collected_count == 3
    assert any(skip.source_key == old.source_key for skip in first.skipped)
    assert second.collected_count == 0
    assert len(second.skipped) == 4
    assert {skip.reason for skip in second.skipped} == {"DUPLICATE-DELTA"}
    ledger = json.loads(
        (root / "demo" / "delta-ledger.json").read_text(encoding="utf-8")
    )
    assert set(ledger["sha256"]) == {_digest(source.content) for source in _sources()}


def test_ledger_deduplicates_across_independent_destinations(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "proposals"
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    first_destination = root / "demo" / "staging" / ("5" * 64)
    second_destination = root / "demo" / "staging" / ("6" * 64)

    first = proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=first_destination, sources=_sources()
    )
    second = proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=second_destination, sources=_sources()
    )

    assert first.collected_count == 3
    assert second.collected_count == 0
    assert {skip.reason for skip in second.skipped} == {"DUPLICATE-DELTA"}
    assert not (second_destination / "delta" / "raw").exists()


def test_production_facade_dates_skip_old_and_collect_fresh_items(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "proposals"
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    cutoff_source = proposal_delta.DeltaSource("note", "note:cutoff.md", b"cutoff")
    _prior_version(root, cutoff_source)
    old = _facade_item(
        "old.md", "SEARCH EXCERPT ONLY", doc_date="2020-01-01",
        date_basis="updated", sha256=_digest(b"DIFFERENT full bytes"),
    )
    fresh = _facade_item(
        "fresh.md", "fresh full payload", doc_date="2026-08-17",
        date_basis="updated", sha256=_digest(b"fresh full payload"),
    )
    destination = root / "demo" / "staging" / ("7" * 64)

    report = proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=destination,
        knowledge=_Facade((old, fresh)),
    )

    assert [item.source_key for item in report.collected] == ["note:fresh.md"]
    assert any(
        skip.source_key == "note:old.md" and skip.reason == "STALE-DELTA"
        for skip in report.skipped
    )


def test_facade_full_content_is_snapshotted_byte_for_byte_and_source_sha_keys_ledger(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "proposals"
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    content = "원문 전체 bytes\n두 번째 줄"
    source_sha = "d" * 64
    item = _facade_item(
        "personal.md", content, doc_date=None, date_basis="none", sha256=source_sha,
    )
    destination = root / "demo" / "staging" / ("8" * 64)

    report = proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=destination,
        knowledge=_Facade((item,)),
    )

    collected = report.collected[0]
    raw = destination / collected.path
    index = json.loads((destination / "delta" / "INDEX.json").read_text(encoding="utf-8"))
    ledger = json.loads((root / "demo" / "delta-ledger.json").read_text(encoding="utf-8"))
    assert raw.read_bytes() == content.encode("utf-8")
    assert index[0]["sha256"] == _digest(content.encode("utf-8"))
    assert ledger["sha256"] == [source_sha]


def test_generic_rag_source_is_note_and_excerpt_fallback_is_marked(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "proposals"
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    excerpt = SimpleNamespace(
        store="rag", source_type="note", ref="FAKE/personal.md",
        summary="SEARCH EXCERPT ONLY", score=0.7,
        doc_date=None, date_basis=None, sha256=None, content=None,
    )
    destination = root / "demo" / "staging" / ("9" * 64)

    report = proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=destination,
        knowledge=_Facade((excerpt,)),
    )

    assert report.collected[0].source_type == "note"
    index = json.loads((destination / "delta" / "INDEX.json").read_text(encoding="utf-8"))
    assert index[0]["sections"] == ["payload:excerpt"]
    assert (destination / report.collected[0].path).read_bytes() == b"SEARCH EXCERPT ONLY"


def test_raw_text_never_enters_indexes_or_changelog(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "proposals"
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))  # type: ignore[attr-defined]
    destination = root / "demo" / "staging" / ("3" * 64)
    changelog = root / "demo" / "changelog.json"
    changelog.parent.mkdir(parents=True)
    changelog.write_text('[{"version":"v000001"}]\n', encoding="utf-8")
    before = changelog.read_bytes()

    proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=destination, sources=_sources()
    )

    index_bytes = (destination / "delta" / "INDEX.json").read_bytes()
    assert all(source.content not in index_bytes for source in _sources())
    assert changelog.read_bytes() == before


def test_malformed_sources_are_explicitly_skipped(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "proposals"
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))  # type: ignore[attr-defined]
    destination = root / "demo" / "staging" / ("4" * 64)
    malformed = (
        proposal_delta.DeltaSource("meeting", "note:meetings/empty.md", b""),
        proposal_delta.DeltaSource("wiki", "wiki:" + "x" * 600, b"valid bytes"),
    )

    report = proposal_delta.collect_deltas(
        "demo", since_version="v000001", dest_dir=destination, sources=malformed
    )

    assert report.collected_count == 0
    assert {skip.reason for skip in report.skipped} == {"INVALID-DELTA"}
    assert not (destination / "changelog.json").exists()

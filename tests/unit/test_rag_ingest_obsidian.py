from __future__ import annotations

from pathlib import Path

from automation.rag_ingest.documents import LogicalDocument
from automation.rag_ingest.sources.obsidian import DEFAULT_EXCLUDE_NAMES, scan_obsidian

PERSPECTIVE = {
    "agent_id": "agent",
    "owner": "cha",
    "role": "personal-research-agent",
    "project": "autophagy",
    "interest_tags": "autophagy,rag",
}


def _write(root: Path, relative: str, text: str = "본문") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _build_vault(root: Path) -> None:
    _write(root, "000_PARA/Archive/x.md", "# 노트 x\n내용")
    _write(root, "001_KIMM_PARA/y.md", "# 노트 y\n내용")
    _write(root, "inbox.md", "루트 노트")
    _write(root, ".obsidian/config", "{}")
    _write(root, ".omo/z.md")
    _write(root, ".sisyphus/s.md")
    _write(root, ".omo-backup-20260521-145418/b.md")
    _write(root, "999_limbo/w.md")
    _write(root, "Excalidraw/e.md")
    _write(root, ".claude/c.md")
    _write(root, ".cursor/k.md")
    _write(root, ".playwright-mcp/p.md")


def _scan(root: Path) -> tuple[list[LogicalDocument], set[str]]:
    return scan_obsidian(root, DEFAULT_EXCLUDE_NAMES, PERSPECTIVE, 1500)


def test_scan_obsidian_present_keys_exactly_reflect_non_excluded_files(
    tmp_path: Path,
) -> None:
    # Given — real PARA notes next to every noise directory from the plan
    _build_vault(tmp_path)

    # When
    documents, present = _scan(tmp_path)

    # Then — noise dirs contribute 0 documents AND 0 present keys
    assert present == {
        "obsidian:000_PARA/Archive/x.md",
        "obsidian:001_KIMM_PARA/y.md",
        "obsidian:inbox.md",
    }
    assert {document.source_key for document in documents} == present


def test_scan_obsidian_source_key_and_folder_metadata(tmp_path: Path) -> None:
    # Given
    _build_vault(tmp_path)

    # When
    documents, _present = _scan(tmp_path)

    # Then — source_key = obsidian:<relpath>, folder = top-level PARA dir
    by_key = {document.source_key: document for document in documents}
    note_x = by_key["obsidian:000_PARA/Archive/x.md"]
    assert note_x.chunks[0].metadata["folder"] == "000_PARA"
    assert note_x.chunks[0].metadata["path"] == "000_PARA/Archive/x.md"
    assert note_x.chunks[0].metadata["source_type"] == "obsidian"
    assert note_x.chunks[0].metadata["agent_id"] == "agent"
    assert note_x.chunks[0].metadata["chunk_index"] == "0"
    note_y = by_key["obsidian:001_KIMM_PARA/y.md"]
    assert note_y.chunks[0].metadata["folder"] == "001_KIMM_PARA"
    root_note = by_key["obsidian:inbox.md"]
    assert "folder" not in root_note.chunks[0].metadata


def test_scan_obsidian_expands_star_suffix_patterns_beyond_dot_filter(
    tmp_path: Path,
) -> None:
    # Given — a non-dot glob so the built-in dot-dir filter cannot mask a
    # broken expansion, plus the plan-mandated .omo-backup* pattern
    _write(tmp_path, "trash-snapshots-2026/t.md")
    _write(tmp_path, ".omo-backup-20260521-145418/b.md")
    _write(tmp_path, "000_PARA/keep.md", "keep")

    # When
    _documents, present = scan_obsidian(
        tmp_path, (".omo-backup*", "trash-*"), PERSPECTIVE, 1500
    )

    # Then
    assert present == {"obsidian:000_PARA/keep.md"}


def test_scan_obsidian_excludes_noise_dirs_at_any_depth(tmp_path: Path) -> None:
    # Given — an Excalidraw dir nested inside a PARA dir
    _write(tmp_path, "000_PARA/Excalidraw/drawing.md")
    _write(tmp_path, "000_PARA/note.md", "노트")

    # When
    _documents, present = _scan(tmp_path)

    # Then
    assert present == {"obsidian:000_PARA/note.md"}


def test_scan_obsidian_two_scans_are_identical(tmp_path: Path) -> None:
    # Given
    _build_vault(tmp_path)

    # When — two consecutive scans of the same tree
    first_documents, first_present = _scan(tmp_path)
    second_documents, second_present = _scan(tmp_path)

    # Then — identical chunks, point ids and fingerprints (idempotent upsert)
    def snapshot(documents: list[LogicalDocument]) -> list[object]:
        return [
            (
                document.source_key,
                document.fingerprint,
                document.point_ids,
                [(chunk.source, chunk.content, chunk.metadata) for chunk in document.chunks],
            )
            for document in documents
        ]

    assert first_present == second_present
    assert snapshot(first_documents) == snapshot(second_documents)


def test_scan_obsidian_normalizes_only_explicit_frontmatter_and_callout_dates(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "000_PARA/frontmatter.md",
        "---\ncreated: 2026-08-19T10:00:00Z\nmodified: 2026/08/20\n---\n본문",
    )
    _write(
        tmp_path,
        "000_PARA/callout.md",
        "# 노트\n\n>[!info]\n> Created: 2026-08-17\n> Updated: 2026-08-18 09:30\n",
    )

    documents, _present = _scan(tmp_path)
    by_key = {document.source_key: document.chunks[0].metadata for document in documents}

    assert by_key["obsidian:000_PARA/frontmatter.md"]["event_date"] == "2026-08-19"
    assert by_key["obsidian:000_PARA/frontmatter.md"]["document_updated"] == "2026-08-20"
    assert by_key["obsidian:000_PARA/callout.md"]["event_date"] == "2026-08-17"
    assert by_key["obsidian:000_PARA/callout.md"]["document_updated"] == "2026-08-18"


def test_scan_obsidian_never_promotes_path_or_file_mtime_to_event_date(
    tmp_path: Path,
) -> None:
    note = tmp_path / "000_PARA" / "20260816.md"
    _write(tmp_path, "000_PARA/20260816.md", "날짜 없는 본문")
    note.touch()

    documents, _present = _scan(tmp_path)
    metadata = documents[0].chunks[0].metadata

    assert metadata["date_basis"] == "path"
    assert "event_date" not in metadata
    assert "document_updated" not in metadata


def test_scan_obsidian_ingests_obsidian_syntax_as_plain_text(tmp_path: Path) -> None:
    # Given — callout / comment / inline-expression syntax under alien frontmatter
    text = (
        "---\n"
        "obsidian-weird: {nested: [broken\n"
        "---\n"
        ">[!info] 콜아웃\n"
        "%%todoist%% 할일\n"
        "=dateformat(file.mtime)\n"
    )
    _write(tmp_path, "000_PARA/callout.md", text)

    # When
    documents, present = _scan(tmp_path)

    # Then — ingested verbatim, no Obsidian parsing
    assert present == {"obsidian:000_PARA/callout.md"}
    content = documents[0].chunks[0].content
    assert ">[!info] 콜아웃" in content
    assert "%%todoist%% 할일" in content
    assert "=dateformat(file.mtime)" in content


def test_scan_obsidian_unclosed_frontmatter_ingests_full_text(tmp_path: Path) -> None:
    # Given — frontmatter fence never closed (parse_frontmatter is tolerant)
    _write(tmp_path, "000_PARA/broken.md", "---\ntitle: 깨진 노트\n본문이 곧바로 이어짐")

    # When
    documents, _present = _scan(tmp_path)

    # Then — whole text survives as plain content, nothing swallowed
    content = documents[0].chunks[0].content
    assert "본문이 곧바로 이어짐" in content
    assert "---" in content


def test_scan_obsidian_empty_note_present_but_undocumented(tmp_path: Path) -> None:
    # Given — deletion sync must still see a file with no chunkable body
    _write(tmp_path, "000_PARA/empty.md", "")

    # When
    documents, present = _scan(tmp_path)

    # Then
    assert present == {"obsidian:000_PARA/empty.md"}
    assert documents == []


def test_scan_obsidian_removed_note_drops_from_present_keys(tmp_path: Path) -> None:
    # Given
    _build_vault(tmp_path)
    _before_documents, before = _scan(tmp_path)
    assert "obsidian:001_KIMM_PARA/y.md" in before

    # When — the owner deletes a note from the vault mirror
    (tmp_path / "001_KIMM_PARA" / "y.md").unlink()
    _after_documents, after = _scan(tmp_path)

    # Then — stale key gone so the pipeline can delete its vectors
    assert after == before - {"obsidian:001_KIMM_PARA/y.md"}


def test_scan_obsidian_missing_mirror_dir_yields_nothing(tmp_path: Path) -> None:
    documents, present = scan_obsidian(
        tmp_path / "없는-미러", DEFAULT_EXCLUDE_NAMES, PERSPECTIVE, 1500
    )
    assert documents == []
    assert present == set()

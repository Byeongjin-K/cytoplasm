from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from automation.rag_ingest import queuefile, statefile
from automation.rag_ingest.config import DiscordSourceConfig, IngestConfig
from automation.rag_ingest.mcp_client import McpUnreachableError
from automation.rag_ingest.pipeline import run_pipeline
from automation.rag_ingest.hashing import document_id

PERSPECTIVE = {
    "agent_id": "agent",
    "owner": "cha",
    "role": "personal-research-agent",
    "project": "autophagy",
    "interest_tags": "autophagy,rag",
}


class FakeMcpClient:
    """In-memory stand-in for the MCP memory server (uuid5 upsert semantics)."""

    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable
        self.points: dict[str, dict[str, Any]] = {}
        self.load_calls = 0

    def load_memory(
        self, content: str, source: str, metadata: dict[str, str]
    ) -> dict[str, Any]:
        if not self.reachable:
            raise McpUnreachableError("fake: connection refused")
        self.load_calls += 1
        from automation.rag_ingest.hashing import document_id

        point_id = document_id(source, content)
        self.points[point_id] = {"content": content, "source": source, "metadata": metadata}
        return {"collection": "personal_cha", "document_id": point_id, "chunk_id": "x"}

    def delete_memory(self, document_id: str) -> dict[str, Any]:
        if not self.reachable:
            raise McpUnreachableError("fake: connection refused")
        self.points.pop(document_id, None)
        return {"deleted": True, "document_id": document_id}


def make_config(tmp_path: Path, discord: DiscordSourceConfig | None = None) -> IngestConfig:
    for name in ("wiki", "notes", "notes/meetings"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return IngestConfig(
        mcp_base_url="http://fake:8765",
        api_key="test-key",
        state_dir=tmp_path / "state",
        wiki_dir=tmp_path / "wiki",
        notes_dir=tmp_path / "notes",
        meetings_dir=tmp_path / "notes" / "meetings",
        hermes_db=None,
        perspective=PERSPECTIVE,
        discord=discord,
    )


LOCAL_SOURCES = {"wiki", "notes", "meetings"}


def test_new_wiki_note_is_ingested(tmp_path: Path) -> None:
    # Given
    config = make_config(tmp_path)
    _ = (config.wiki_dir / "a.md").write_text("고유한 위키 내용", encoding="utf-8")
    client = FakeMcpClient()

    # When
    pending, log_lines = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]

    # Then
    assert pending == 0
    assert len(client.points) == 1
    assert any("INGESTED wiki:a.md" in line for line in log_lines)


def test_reingest_unchanged_note_adds_zero_vectors_and_zero_calls(tmp_path: Path) -> None:
    # Given
    config = make_config(tmp_path)
    _ = (config.wiki_dir / "a.md").write_text("고유한 위키 내용", encoding="utf-8")
    client = FakeMcpClient()
    _ = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]
    count_before, calls_before = len(client.points), client.load_calls

    # When — same document again
    pending, _ = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]

    # Then — fingerprint skip: no network call, no new vector
    assert pending == 0
    assert client.load_calls == calls_before
    assert len(client.points) == count_before


def test_forced_reingest_still_adds_zero_vectors_via_uuid5_upsert(tmp_path: Path) -> None:
    # Given
    config = make_config(tmp_path)
    _ = (config.wiki_dir / "a.md").write_text("고유한 위키 내용", encoding="utf-8")
    client = FakeMcpClient()
    _ = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]
    count_before = len(client.points)

    # When — bypass client-side dedup entirely
    _ = run_pipeline(config, LOCAL_SOURCES, force=True, client=client)  # type: ignore[arg-type]

    # Then — server-side upsert overwrites the same point ids
    assert len(client.points) == count_before


def test_changed_note_replaces_stale_points(tmp_path: Path) -> None:
    # Given
    config = make_config(tmp_path)
    note = config.wiki_dir / "a.md"
    _ = note.write_text("첫 버전", encoding="utf-8")
    client = FakeMcpClient()
    _ = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]
    old_ids = set(client.points)

    # When
    _ = note.write_text("두 번째 버전", encoding="utf-8")
    _ = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]

    # Then — old vector deleted, exactly one current vector remains
    assert len(client.points) == 1
    assert not old_ids & set(client.points)


def test_deleted_file_removes_its_vectors(tmp_path: Path) -> None:
    # Given
    config = make_config(tmp_path)
    note = config.wiki_dir / "a.md"
    _ = note.write_text("삭제될 노트", encoding="utf-8")
    client = FakeMcpClient()
    _ = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]
    assert len(client.points) == 1

    # When
    note.unlink()
    _ = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]

    # Then
    assert len(client.points) == 0
    state = statefile.load_state(config.state_path)
    assert "wiki:a.md" not in state["documents"]


def test_excluded_research_trends_folder_removes_ingested_note(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    folder_name = "research-trends"
    folder = config.notes_dir / folder_name
    folder.mkdir()
    _ = (folder / "x.md").write_text("기존 노트", encoding="utf-8")
    source_key = f"note:{folder_name}/x.md"
    point = document_id(source_key + "#c0000", "기존 노트")
    client = FakeMcpClient()
    client.points[point] = {"content": "기존 노트", "source": source_key + "#c0000", "metadata": {}}
    state = statefile.empty_state()
    statefile.record_document(state, source_key, "old-fingerprint", [point], "2026-01-01T00:00:00Z")
    statefile.save_state(config.state_path, state)

    # Simulate the old folder still existing after the feature moved elsewhere.
    # Its scan exclusion makes the old key absent and queues its vector deletion.
    _ = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]

    assert source_key not in statefile.load_state(config.state_path)["documents"]
    assert client.points == {}


def test_rag_down_queues_then_recovery_delivers_without_loss(tmp_path: Path) -> None:
    # Given — RAG node down during ingest
    config = make_config(tmp_path)
    _ = (config.wiki_dir / "a.md").write_text("장애 중 작성된 노트", encoding="utf-8")
    down_client = FakeMcpClient(reachable=False)

    # When
    pending, log_lines = run_pipeline(config, LOCAL_SOURCES, client=down_client)  # type: ignore[arg-type]

    # Then — job queued, nothing lost, state NOT advanced
    assert pending == 1
    assert any(line.startswith("QUEUED") for line in log_lines)
    assert queuefile.load_jobs(config.queue_path)
    assert statefile.load_state(config.state_path)["documents"] == {}

    # When — recovery: next tick with reachable node
    up_client = FakeMcpClient()
    pending_after, _ = run_pipeline(config, LOCAL_SOURCES, client=up_client)  # type: ignore[arg-type]

    # Then — queue drained, vector present, state advanced (0 loss)
    assert pending_after == 0
    assert len(up_client.points) == 1
    assert queuefile.load_jobs(config.queue_path) == []
    assert "wiki:a.md" in statefile.load_state(config.state_path)["documents"]


def test_meeting_doc_carries_my_perspective_metadata(tmp_path: Path) -> None:
    # Given
    config = make_config(tmp_path)
    _ = (config.meetings_dir / "회의.md").write_text("결정사항 요약", encoding="utf-8")
    client = FakeMcpClient()

    # When
    _ = run_pipeline(config, LOCAL_SOURCES, client=client)  # type: ignore[arg-type]

    # Then
    (point,) = client.points.values()
    assert point["metadata"]["source_type"] == "meeting"
    assert point["metadata"]["agent_id"] == "agent"
    assert point["metadata"]["role"] == "personal-research-agent"
    assert point["metadata"]["interest_tags"] == "autophagy,rag"


def test_queue_file_is_owner_only(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "queue.jsonl"

    # When
    queuefile.save_jobs(path, [queuefile.make_job("k", "fp", [], [], [], {}, "t")])

    # Then — queued payloads may hold sensitive content
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text().splitlines()[0])["source_key"] == "k"

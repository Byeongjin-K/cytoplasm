"""Ingest pipeline: scan sources -> diff against state -> queue -> deliver.

Dedup layers:
  1. client fingerprint (state.json): unchanged documents are skipped before
     any network call;
  2. server uuid5(source, content) upsert: even a forced re-ingest overwrites
     the same points (0 duplicates).

Failure path: delivery stops at the first McpUnreachableError; undelivered
jobs stay in queue.jsonl and are retried on the next cron tick. State and
cursors advance only after successful delivery (0 loss, at-least-once with
idempotent upserts).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import queuefile, statefile
from .config import IngestConfig
from .documents import LogicalDocument
from .mcp_client import McpMemoryClient, McpUnreachableError
from .sources.conversations import scan_conversations
from .sources.discord_team import DiscordFetchError, scan_discord
from .sources.files import scan_directory
from .sources.obsidian import ObsidianSyncError, mirror_is_healthy, scan_obsidian, sync_mirror

# Weekly research trends moved off the node (owner 2026-10-21); drop its old vectors.
_RETIRED_NOTE_DIR = "research-trends"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scan_obsidian_source(
    config: IngestConfig, log_lines: list[str]
) -> tuple[list[LogicalDocument], set[str]] | None:
    """Sync the vault mirror and scan it; ``None`` = source skipped this run.

    Codified decision 2: a sync failure NEVER aborts the pipeline — WARN and
    scan the last-good mirror when a HEALTHY one exists (staleness acceptable),
    otherwise skip the obsidian source for this run. A partial clone (``.git``
    without a resolvable HEAD) is NOT a usable mirror.
    """
    obsidian = config.obsidian
    if obsidian is None:
        return None
    try:
        _ = sync_mirror(obsidian)
    except ObsidianSyncError as error:
        if not mirror_is_healthy(obsidian.mirror_dir):
            log_lines.append(f"WARN obsidian source skipped this run (no usable mirror): {error}")
            return None
        log_lines.append(f"WARN obsidian sync failed, scanning last-good mirror: {error}")
    try:
        return scan_obsidian(
            obsidian.mirror_dir,
            obsidian.exclude_names,
            config.perspective,
            config.max_chunk_chars,
        )
    except ObsidianSyncError as error:
        log_lines.append(f"WARN obsidian source skipped this run: {error}")
        return None


def collect_documents(
    config: IngestConfig,
    state: dict[str, Any],
    pending_keys: set[str],
    sources: set[str],
    network_log: list[str],
    log_lines: list[str],
) -> tuple[list[LogicalDocument], dict[str, set[str]]]:
    """Gather documents from all enabled sources.

    Returns (documents, {prefix: present_keys}) — present key sets drive
    deletion of vectors whose backing file disappeared.
    """
    documents: list[LogicalDocument] = []
    present: dict[str, set[str]] = {}
    if "wiki" in sources:
        wiki_documents, wiki_keys = scan_directory(
            config.wiki_dir, "wiki", "wiki", config.perspective, config.max_chunk_chars
        )
        documents.extend(wiki_documents)
        present["wiki"] = wiki_keys
    if "notes" in sources:
        note_documents, note_keys = scan_directory(
            config.notes_dir, "note", "note", config.perspective, config.max_chunk_chars,
            exclude_dirs=(config.meetings_dir, config.notes_dir / _RETIRED_NOTE_DIR),
        )
        documents.extend(note_documents)
        present["note"] = note_keys
    if "meetings" in sources:
        meeting_documents, meeting_keys = scan_directory(
            config.meetings_dir, "meeting", "meeting", config.perspective,
            config.max_chunk_chars,
        )
        documents.extend(meeting_documents)
        present["meeting"] = meeting_keys
    if "conversations" in sources and config.hermes_db is not None:
        documents.extend(
            scan_conversations(config.hermes_db, config.perspective, config.max_chunk_chars)
        )
    if "discord" in sources and config.discord is not None:
        try:
            documents.extend(scan_discord(config, state, pending_keys, network_log))
        except DiscordFetchError as error:
            log_lines.append(f"WARN discord source skipped this run: {error}")
    if "obsidian" in sources and config.obsidian is not None:
        obsidian_scan = _scan_obsidian_source(config, log_lines)
        if obsidian_scan is not None:
            obsidian_documents, obsidian_keys = obsidian_scan
            documents.extend(obsidian_documents)
            present["obsidian"] = obsidian_keys
    return documents, present


def plan_jobs(
    documents: list[LogicalDocument],
    present: dict[str, set[str]],
    state: dict[str, Any],
    pending_keys: set[str],
    force: bool,
) -> list[dict[str, Any]]:
    """Diff documents against state; emit queue jobs for changes only."""
    jobs: list[dict[str, Any]] = []
    created_at = _now_iso()
    for document in documents:
        if document.source_key in pending_keys:
            continue
        if not document.chunks:
            if document.cursor_updates:
                jobs.append(
                    queuefile.make_job(
                        document.source_key, "", [], [], [], document.cursor_updates,
                        created_at,
                    )
                )
            continue
        fingerprint = document.fingerprint
        previous_fingerprint = statefile.document_fingerprint(state, document.source_key)
        if not force and previous_fingerprint == fingerprint:
            continue
        new_point_ids = document.point_ids
        stale_ids = [
            point_id
            for point_id in statefile.document_point_ids(state, document.source_key)
            if point_id not in set(new_point_ids)
        ]
        upserts = [
            {"source": chunk.source, "content": chunk.content, "metadata": chunk.metadata}
            for chunk in document.chunks
        ]
        jobs.append(
            queuefile.make_job(
                document.source_key, fingerprint, upserts, stale_ids, new_point_ids,
                document.cursor_updates, created_at,
            )
        )
    for prefix, present_keys in present.items():
        for source_key in sorted(state["documents"]):
            if not source_key.startswith(f"{prefix}:"):
                continue
            if source_key in present_keys or source_key in pending_keys:
                continue
            stale_ids = statefile.document_point_ids(state, source_key)
            jobs.append(
                queuefile.make_job(source_key, "", [], stale_ids, [], {}, created_at)
            )
    return jobs


def deliver_jobs(
    jobs: list[dict[str, Any]],
    client: McpMemoryClient,
    state: dict[str, Any],
    config: IngestConfig,
    log_lines: list[str],
) -> tuple[list[dict[str, Any]], int]:
    """Drain jobs oldest-first. Returns (remaining jobs, delivered count)."""
    remaining = list(jobs)
    delivered = 0
    while remaining:
        job = remaining[0]
        try:
            for stale_id in job["deletes"]:
                client.delete_memory(str(stale_id))
            for upsert in job["upserts"]:
                client.load_memory(
                    str(upsert["content"]), str(upsert["source"]), dict(upsert["metadata"])
                )
        except McpUnreachableError as error:
            log_lines.append(
                f"QUEUED rag unreachable, {len(remaining)} job(s) kept for retry: {error}"
            )
            break
        source_key = str(job["source_key"])
        if job["upserts"]:
            statefile.record_document(
                state, source_key, str(job["fingerprint"]),
                [str(point_id) for point_id in job["point_ids"]], _now_iso(),
            )
            log_lines.append(
                f"INGESTED {source_key} chunks={len(job['upserts'])} "
                f"deletes={len(job['deletes'])} fp={str(job['fingerprint'])[:12]}"
            )
        elif job["deletes"]:
            statefile.remove_document(state, source_key)
            log_lines.append(f"REMOVED {source_key} deletes={len(job['deletes'])}")
        cursor_updates = dict(job.get("cursor_updates", {}))
        if cursor_updates:
            statefile.apply_cursor_updates(state, cursor_updates)
            log_lines.append(f"CURSOR {source_key} {sorted(cursor_updates)}")
        statefile.save_state(config.state_path, state)
        remaining.pop(0)
        delivered += 1
    return remaining, delivered


def run_pipeline(
    config: IngestConfig,
    sources: set[str],
    force: bool = False,
    client: McpMemoryClient | None = None,
) -> tuple[int, list[str]]:
    """Full run. Returns (pending job count after run, log lines)."""
    log_lines: list[str] = []
    network_log: list[str] = []
    state = statefile.load_state(config.state_path)
    queued = queuefile.load_jobs(config.queue_path)
    pending_keys = queuefile.pending_source_keys(queued)

    documents, present = collect_documents(
        config, state, pending_keys, sources, network_log, log_lines
    )
    new_jobs = plan_jobs(documents, present, state, pending_keys, force)
    if new_jobs:
        queued = queued + new_jobs
        queuefile.save_jobs(config.queue_path, queued)
        log_lines.append(f"PLANNED {len(new_jobs)} new job(s)")

    active_client = client or McpMemoryClient(
        base_url=config.mcp_base_url, api_key=config.api_key, network_log=network_log
    )
    remaining, delivered = deliver_jobs(queued, active_client, state, config, log_lines)
    queuefile.save_jobs(config.queue_path, remaining)
    if delivered or new_jobs:
        log_lines.append(f"DELIVERED {delivered} job(s), pending {len(remaining)}")
    for url in dict.fromkeys(network_log):
        log_lines.append(f"NETWORK {url}")
    return len(remaining), log_lines

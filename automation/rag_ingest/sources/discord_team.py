"""Team knowledge influx from Discord: #team chat + #agents-log peer reports.

Fetches new guild messages incrementally (cursor per channel, advanced only
after successful vector delivery) through Discord REST. Reports in
#agents-log are parsed with the strict W1-6 v0 schema (exactly 7 keys inside
a JSON code block); only OTHER agents' reports are ingested. Team chat is
ingested as deterministic batch transcripts. Every vector carries this
agent's own perspective metadata (duplication per person is by design).

Discord REST is a *fetch* source, not an embedding path: embedding happens
only inside the RAG node. Gotcha (W1-2/W1-6): Discord REST 403s Python's
default urllib User-Agent — a DiscordBot-form User-Agent is mandatory.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from automation import group_roster

from ..chunking import chunk_markdown
from ..documents import LogicalDocument, build_document
from ..metadata import build_metadata
from .. import config as config_module

_API_BASE = "https://discord.com/api/v10"
_USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
_REPORT_KEYS = {"version", "agent_id", "task_id", "status", "summary", "links", "timestamp"}
_MAX_TEAM_MESSAGES = 100
_MAX_CHARS_PER_MESSAGE = 500
_ROSTER_ENV = "AUTOPHAGY_ROSTER"

LOGGER = logging.getLogger("autophagy.rag_ingest.discord_team")


class DiscordFetchError(Exception):
    """Discord REST failure — the run skips Discord sources and retries later."""


def _request(path: str, token: str, network_log: list[str]) -> Any:
    url = f"{_API_BASE}{path}"
    network_log.append(url)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise DiscordFetchError(f"discord fetch failed for {path}: {error}") from error


def _resolve_channels(
    discord_config: config_module.DiscordSourceConfig,
    token: str,
    cache: dict[str, str],
    network_log: list[str],
) -> dict[str, str]:
    wanted = {name for name in (discord_config.team_channel, discord_config.agents_log_channel) if name}
    if wanted.issubset(cache.keys()):
        return cache
    channels = _request(f"/guilds/{discord_config.guild_id}/channels", token, network_log)
    resolved = dict(cache)
    for channel in channels:
        name = str(channel.get("name", ""))
        if name in wanted:
            resolved[name] = str(channel["id"])
    missing = wanted - resolved.keys()
    if missing:
        raise DiscordFetchError(f"channels not found in guild: {sorted(missing)}")
    return resolved


def _fetch_new_messages(
    channel_id: str,
    cursor: str | None,
    bootstrap_limit: int,
    token: str,
    network_log: list[str],
) -> list[dict[str, Any]]:
    if cursor:
        query = urllib.parse.urlencode({"after": cursor, "limit": 100})
    else:
        query = urllib.parse.urlencode({"limit": bootstrap_limit})
    messages = _request(f"/channels/{channel_id}/messages?{query}", token, network_log)
    return sorted(messages, key=lambda message: int(message["id"]))


def parse_peer_report(message_content: str) -> dict[str, Any] | None:
    """Strict W1-6 v0 report parse: JSON code block with exactly the 7 keys."""
    stripped = message_content.strip()
    if not stripped.startswith("```json") or not stripped.endswith("```"):
        return None
    body = stripped[len("```json") : -len("```")].strip()
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict) or set(payload) != _REPORT_KEYS:
        return None
    if payload.get("version") != "v0":
        return None
    return payload


def _report_documents(
    messages: list[dict[str, Any]],
    self_id: str,
    channel_key: str,
    perspective: dict[str, str],
    max_chunk_chars: int,
) -> list[LogicalDocument]:
    raw_roster_path = os.environ.get(_ROSTER_ENV, "").strip()
    roster_path = Path(raw_roster_path) if raw_roster_path else group_roster.DEFAULT_ROSTER_PATH
    try:
        roster = group_roster.load_roster(roster_path.expanduser())
    except group_roster.RosterError as error:
        raise DiscordFetchError(f"group roster unavailable: {error}") from error
    documents: list[LogicalDocument] = []
    for message in messages:
        author = message.get("author", {})
        author_id = str(author.get("id"))
        if author_id == self_id:
            continue
        report = parse_peer_report(str(message.get("content", "")))
        if report is None:
            continue
        claimed_agent_id = str(report["agent_id"])
        expected_agent_id = roster.sender_id_for_discord_author(author_id)
        if expected_agent_id != claimed_agent_id:
            LOGGER.warning(
                "discord peer report rejected reason=sender_identity_mismatch "
                + "message=%s author=%s claimed_agent=%s",
                str(message.get("id", "")),
                f"…{author_id[-4:]}" if len(author_id) > 4 else "<short>",
                claimed_agent_id,
            )
            continue
        links = report.get("links")
        links_text = ", ".join(str(link) for link in links) if isinstance(links, list) else ""
        content = (
            f"# 동료 에이전트 보고 ({report['agent_id']})\n"
            f"task: {report['task_id']}\nstatus: {report['status']}\n"
            f"timestamp: {report['timestamp']}\n\n{report['summary']}"
        )
        if links_text:
            content += f"\n\nlinks: {links_text}"
        source_key = f"agents-log:{message['id']}"
        base_metadata = build_metadata(
            perspective,
            "peer-report",
            {
                "report_agent_id": str(report["agent_id"]),
                "task_id": str(report["task_id"]),
                "status": str(report["status"]),
                "report_timestamp": str(report["timestamp"]),
                "message_id": str(message["id"]),
                "channel": channel_key,
            },
        )
        documents.append(
            build_document(source_key, chunk_markdown(content, max_chunk_chars), base_metadata)
        )
    return documents


def _team_document(
    messages: list[dict[str, Any]],
    channel_key: str,
    perspective: dict[str, str],
    max_chunk_chars: int,
) -> LogicalDocument | None:
    lines: list[str] = []
    for message in messages[:_MAX_TEAM_MESSAGES]:
        content = " ".join(str(message.get("content", "")).split())
        if not content:
            continue
        author = message.get("author", {})
        display = str(author.get("global_name") or author.get("username") or "unknown")
        lines.append(f"[{display}] {content[:_MAX_CHARS_PER_MESSAGE]}")
    if not lines:
        return None
    first_id = str(messages[0]["id"])
    last_id = str(messages[-1]["id"])
    body = f"# 팀 채널 대화 발췌 (#{channel_key})\n" + "\n".join(lines)
    source_key = f"team:{first_id}-{last_id}"
    base_metadata = build_metadata(
        perspective,
        "team-chat",
        {
            "channel": channel_key,
            "first_message_id": first_id,
            "last_message_id": last_id,
            "message_count": str(len(lines)),
        },
    )
    return build_document(source_key, chunk_markdown(body, max_chunk_chars), base_metadata)


def scan_discord(
    ingest_config: config_module.IngestConfig,
    state: dict[str, Any],
    pending_keys: set[str],
    network_log: list[str],
) -> list[LogicalDocument]:
    """Fetch new messages and emit documents with cursor updates attached."""
    discord_config = ingest_config.discord
    if discord_config is None:
        return []
    if any(key.startswith(("team:", "agents-log:")) for key in pending_keys):
        return []  # queued discord jobs must deliver first so cursors stay consistent

    token = ingest_config.discord_token
    cursors: dict[str, str] = state.get("cursors", {})
    channel_cache_raw = state.get("discord_channels")
    channel_cache = dict(channel_cache_raw) if isinstance(channel_cache_raw, dict) else {}
    channels = _resolve_channels(discord_config, token, channel_cache, network_log)
    state["discord_channels"] = channels

    self_id = str(state.get("discord_self_id", ""))
    if not self_id:
        self_id = str(_request("/users/@me", token, network_log)["id"])
        state["discord_self_id"] = self_id

    documents: list[LogicalDocument] = []
    for channel_name, handler in (
        (discord_config.agents_log_channel, "reports"),
        (discord_config.team_channel, "team"),
    ):
        if not channel_name:
            continue
        channel_id = channels[channel_name]
        cursor_key = f"discord:{channel_name}"
        messages = _fetch_new_messages(
            channel_id,
            cursors.get(cursor_key),
            discord_config.bootstrap_limit,
            token,
            network_log,
        )
        if not messages:
            continue
        cursor_update = {cursor_key: str(messages[-1]["id"])}
        if handler == "reports":
            channel_documents = _report_documents(
                messages, self_id, channel_name, ingest_config.perspective,
                ingest_config.max_chunk_chars,
            )
        else:
            team_document = _team_document(
                messages, channel_name, ingest_config.perspective,
                ingest_config.max_chunk_chars,
            )
            channel_documents = [team_document] if team_document else []
        if not channel_documents:
            # nothing ingestable in the new window: advance cursor via a
            # cursor-only document so state moves after this run's delivery
            documents.append(
                LogicalDocument(
                    source_key=f"cursor:{cursor_key}", chunks=(), cursor_updates=cursor_update
                )
            )
            continue
        channel_documents[-1].cursor_updates.update(cursor_update)
        documents.extend(channel_documents)
    return documents

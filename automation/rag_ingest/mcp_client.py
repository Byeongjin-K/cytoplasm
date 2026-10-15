"""Minimal stdlib client for the W2-1 FastMCP memory server (Streamable HTTP).

Protocol (verified live against the configured RAG node on 2026-07-15):
  1. POST {base}/mcp/ ``initialize``      -> SSE body + ``mcp-session-id`` header
  2. POST ``notifications/initialized``   -> 202
  3. POST ``tools/call``                  -> SSE ``data:`` line with JSON-RPC result

Uses only ``urllib`` so the ingest path has a single, auditable network choke
point. Every connection target is appended to ``network_log`` — the ingest log
records it, and the external-embedding-API==0 proof is additionally taken with
an OS-level ``strace`` capture during QA.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeAlias, cast

_PROTOCOL_VERSION = "2025-06-18"
_TIMEOUT_SECONDS = 30.0

JsonValue: TypeAlias = (
    "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
)


class _HttpResponse(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(self) -> bytes: ...

    def close(self) -> None: ...


class McpUnreachableError(Exception):
    """RAG node down / connection refused / 5xx — retryable, keep queued."""


class McpFatalError(Exception):
    """Auth or protocol failure — not retryable without operator action."""


@dataclass
class McpMemoryClient:
    base_url: str
    api_key: str
    network_log: list[str] = field(default_factory=list)
    _session_id: str | None = field(default=None, init=False)
    _next_id: int = field(default=1, init=False)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id is not None:
            headers["mcp-session-id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, JsonValue]) -> tuple[int, dict[str, str], str]:
        url = f"{self.base_url.rstrip('/')}/mcp/"
        self.network_log.append(url)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            response = cast(
                _HttpResponse,
                cast(object, urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS)),
            )
            try:
                body = response.read().decode("utf-8", errors="replace")
                headers = {k.lower(): v for k, v in response.headers.items()}
                return response.status, headers, body
            finally:
                response.close()
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise McpFatalError(f"MCP auth rejected: HTTP {error.code}") from error
            raise McpUnreachableError(f"MCP HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            raise McpUnreachableError(f"MCP unreachable: {error}") from error

    @staticmethod
    def _parse_sse_result(body: str, request_id: int) -> dict[str, JsonValue]:
        for line in body.splitlines():
            if not line.startswith("data:"):
                continue
            message = cast(
                dict[str, JsonValue],
                cast(object, json.loads(line[len("data:") :].strip())),
            )
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise McpFatalError(f"MCP JSON-RPC error: {message['error']}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise McpFatalError("MCP result missing or malformed")
            return cast(dict[str, JsonValue], result)
        raise McpFatalError("no matching SSE data frame in MCP response")

    def connect(self) -> None:
        request_id = self._next_id
        self._next_id += 1
        _status, headers, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "rag-ingest", "version": "1.0.0"},
                },
            }
        )
        session_id = headers.get("mcp-session-id")
        if not session_id:
            raise McpFatalError("initialize returned no mcp-session-id header")
        _ = self._parse_sse_result(body, request_id)
        self._session_id = session_id
        _status2, _headers2, _body2 = self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

    def _ensure_connected(self) -> None:
        if self._session_id is None:
            self.connect()

    def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        self._ensure_connected()
        request_id = self._next_id
        self._next_id += 1
        _status, _headers, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        result = self._parse_sse_result(body, request_id)
        if result.get("isError"):
            raise McpFatalError(f"tool {name} returned isError: {result.get('content')}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return cast(dict[str, JsonValue], structured).get(
                "result", cast(dict[str, JsonValue], structured)
            )
        return result

    def load_memory(
        self, content: str, source: str, metadata: dict[str, str]
    ) -> dict[str, JsonValue]:
        metadata_json: dict[str, JsonValue] = {
            key: value for key, value in metadata.items()
        }
        loaded = self.call_tool(
            "load_memory",
            {"content": content, "source": source, "metadata": metadata_json},
        )
        if not isinstance(loaded, dict):
            raise McpFatalError("load_memory returned non-dict result")
        return cast(dict[str, JsonValue], loaded)

    def search_memory(
        self,
        query: str,
        limit: int = 5,
        entity_anchors: tuple[str, ...] | None = None,
        *,
        one_per_note: bool = False,
        per_note: int = 1,
        exclude_notes: Sequence[str] = (),
    ) -> list[dict[str, JsonValue]]:
        arguments: dict[str, JsonValue] = {"query": query, "limit": limit}
        if entity_anchors is not None:
            arguments["entity_anchors"] = list(entity_anchors)
        if one_per_note:
            arguments["one_per_note"] = True
        if per_note != 1:
            arguments["per_note"] = per_note
        if exclude_notes:
            arguments["exclude_notes"] = list(exclude_notes)
        found = self.call_tool("search_memory", arguments)
        if not isinstance(found, list):
            raise McpFatalError("search_memory returned non-list result")
        if not all(isinstance(item, dict) for item in found):
            raise McpFatalError("search_memory returned malformed items")
        return [cast(dict[str, JsonValue], item) for item in found]

    def core_notes(self) -> list[dict[str, str]]:
        found = self.call_tool("memory_core_notes", {})
        if not isinstance(found, list):
            raise McpFatalError("memory_core_notes returned non-list result")
        notes: list[dict[str, str]] = []
        for item in found:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(key), str) for key in ("key", "title", "text")
            ):
                raise McpFatalError("memory_core_notes returned malformed items")
            notes.append({key: str(item[key]) for key in ("key", "title", "text")})
        return notes

    def delete_memory(self, document_id: str) -> dict[str, JsonValue]:
        deleted = self.call_tool("delete_memory", {"document_id": document_id})
        if not isinstance(deleted, dict):
            raise McpFatalError("delete_memory returned non-dict result")
        return cast(dict[str, JsonValue], deleted)

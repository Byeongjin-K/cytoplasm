"""Smoke owns HTTP health and authenticated MCP search, separate from Docker apply."""
from __future__ import annotations

import io
import json
import sys
import urllib.request
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("failure", ["", "health", "search"])
def test_rag_stack_smoke_reads_health_and_authenticated_search_without_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    monkeypatch.syspath_prepend(str(_REPO / "automation/rag_ingest"))
    from automation.rag_stack import smoke

    secret = tmp_path / "secrets"
    secret.write_text("MCP_BIND_ADDRESS=127.0.0.1\nRAG_MCP_API_KEY=synthetic\n")
    monkeypatch.setattr(sys, "argv", ["smoke", str(secret), "0"])
    calls = []

    class Response(io.BytesIO):
        status = 200
        headers = {"mcp-session-id": "session"}

    def open_request(request: str | urllib.request.Request, *, timeout: float) -> Response:
        assert timeout > 0
        if isinstance(request, str):
            calls.append("health")
            return Response(json.dumps({"status": "failed" if failure == "health" else "ok"}).encode())
        assert request.get_header("Authorization") == "Bearer synthetic"
        payload = json.loads(request.data or b"{}")
        calls.append(payload["method"])
        if payload["method"] == "notifications/initialized":
            return Response(b"")
        if payload["method"] == "tools/call":
            assert payload["params"]["name"] == "search_memory"
            assert payload["params"]["arguments"]["limit"] == 1
        body = {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"structuredContent": {"result": []}}}
        if failure == "search" and payload["method"] == "tools/call":
            body["result"] = {"isError": True}
        return Response(("data: " + json.dumps(body) + "\n").encode())

    monkeypatch.setattr(urllib.request, "urlopen", open_request)

    result = smoke.main()

    assert result == (1 if failure else 0)
    assert calls == (["health"] if failure == "health" else [
        "health", "initialize", "notifications/initialized", "tools/call",
    ])

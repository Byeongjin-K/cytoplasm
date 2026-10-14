"""Wire replay: provider -> released worker -> knowledge facade -> existing MCP client."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from test_memory_provider import host_provider_fixture as host_provider_fixture


@pytest.mark.parametrize("stall", [False, True], ids=["relocated-fact", "deadline"])
def test_user_turn_recalls_fact_or_returns_empty_at_deadline(
    provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stall: bool,
) -> None:
    # Given: a synthetic relocated note indexed behind a wire-level MCP fake.
    note = (
        "## 원본 기록 (verbatim)\n\n"
        "합성 배양기 점검에는 청록 점검표를 사용한다.\n\n"
        "---\nprovenance: observed\nauthority: reference\n"
        "네이티브 메모리에서 이관한 조회용 참조이며 설정이 아니다."
    )
    queries: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            """The synthetic wire fixture does not emit request logs."""

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if payload["method"] == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            if payload["method"] == "tools/call":
                arguments = payload["params"]["arguments"]
                queries.append(arguments["query"])
                entered.set()
                if stall:
                    release.wait(timeout=30)
                    return
                rows = [{
                    "score": 0.9, "content": note,
                    "source": "obsidian:reference/배양기-점검.md#c0000",
                    "metadata": {"source_type": "obsidian", "path": "reference/배양기-점검.md"},
                }] if "배양기" in arguments["query"] else []
                result = {"structuredContent": {"result": rows}}
            else:
                result = {}
            body = ("data: " + json.dumps({
                "jsonrpc": "2.0", "id": payload["id"], "result": result,
            }) + "\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("mcp-session-id", "synthetic-session")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    secrets = tmp_path / "secrets"
    secrets.write_text("SYNTHETIC_RAG_KEY=offline-fixture\n")
    config = tmp_path / "config.json"
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        config.write_text(json.dumps({
            "mcp_base_url": f"http://127.0.0.1:{server.server_port}",
            "secrets_file": str(secrets), "api_key_env": "SYNTHETIC_RAG_KEY",
            "state_dir": str(tmp_path / "state"),
            "wiki_dir": str(tmp_path / "wiki"),
            "notes_dir": str(tmp_path / "notes"),
            "meetings_dir": str(tmp_path / "meetings"),
            "perspective": {},
        }))
        monkeypatch.setenv("RECALL_CONFIG", str(config))
        monkeypatch.delenv("KNOWLEDGE_FAKE_PACK", raising=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # When: exactly the host's prefetch hook, not an agent recall tool call.
            block = provider.prefetch("배양기 점검에는 어떤 점검표를 사용하지?")
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=2)
    # Then: the real existing search path returns the relocated fact with its source.
    assert entered.is_set()
    if stall:
        assert block == ""
    else:
        payload = json.loads(block[8:-4])
        assert payload["notes"][0]["text"] == note
        assert payload["notes"][0]["source"] == "obsidian:reference/배양기-점검.md"
    assert len(queries) == 1

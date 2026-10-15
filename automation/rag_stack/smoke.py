"""Bounded local health + authenticated search read-back; never print credentials/results."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from mcp_client import McpFatalError, McpMemoryClient, McpUnreachableError


def main() -> int:
    values: dict[str, str] = {}
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep:
            values[key] = value.strip().strip("'\"")
    address = values.get("MCP_BIND_ADDRESS", "")
    key = values.get("RAG_MCP_API_KEY", "")
    if not address or not key:
        print("RAG-MCP-SMOKE-FAIL: configuration", file=sys.stderr)
        return 1
    url = f"http://{address}:8765"
    deadline = time.monotonic() + int(sys.argv[2])
    while True:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
                healthy = json.load(response).get("status") == "ok"
        except (OSError, urllib.error.URLError, ValueError):
            healthy = False
        if healthy:
            break
        if time.monotonic() >= deadline:
            print("RAG-MCP-SMOKE-FAIL: health", file=sys.stderr)
            return 1
        time.sleep(1)
    client = McpMemoryClient(url, key)
    # The restored previous image predates the recall arguments, so a rollback is checked with search only.
    basic = len(sys.argv) > 3 and sys.argv[3] == "basic"
    checks = (
        ("search", lambda: client.search_memory("release read-back", limit=1)),
        ("recall-search", lambda: client.search_memory(
            "release read-back", limit=1, one_per_note=True, per_note=2, exclude_notes=["release:read-back"],
        )),
        ("core-notes", client.core_notes),
    )
    for name, check in checks[:1] if basic else checks:
        try:
            check()
        except (McpFatalError, McpUnreachableError, ValueError):
            print(f"RAG-MCP-SMOKE-FAIL: {name}", file=sys.stderr)
            return 1
    print("RAG-MCP-SMOKE-PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

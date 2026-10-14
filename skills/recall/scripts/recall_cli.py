"""recall_cli — W2-5 recall skill CLI (RAG search with source attribution).

Usage:
    python3 recall_cli.py search "<query>" [--limit N] [--threshold F]
                                           [--strong-threshold F] [--json]

Behavior contract (W2-5): retrieval is local MCP only and one attempt per
search with no retry. The default is one search; ``--entity-fallback`` permits
one entity-anchor fallback after ``no_memory``. RAG down means ``unavailable``.
  * empty/below-threshold => ``no_memory``; answer "기억 없음", never fabricate.
  * every search appends one masked line to a mode-600 log under
    ``~/.hermes/recall/logs/`` (override: RECALL_LOG_DIR).

Offline test hooks (sandbox scenario / unit tests only — no network):
  RECALL_FAKE_RESULTS=<path.json>  use these rows instead of MCP search
  RECALL_FAKE_ERROR=unreachable    simulate a down RAG node
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import recall_runtime  # noqa: E402 - resolved from the script dir inserted above.

sys.path.insert(0, str(recall_runtime.runtime_root()))

import recall_core  # noqa: E402
import recall_pick  # noqa: E402

_RUNTIME_DEFAULT = "~/.hermes/rag_ingest_runtime"
_CONFIG_DEFAULT = "~/.hermes/rag-ingest/config.json"
_LOG_DIR_DEFAULT = "~/.hermes/recall/logs"
_PICKED = "pick:whole-store"
analyze_entity_intent = recall_core.analyze_entity_intent


def _log_line(response: dict[str, Any], network_log: list[str]) -> None:
    log_dir = Path(os.environ.get("RECALL_LOG_DIR", _LOG_DIR_DEFAULT)).expanduser()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"recall-{time.strftime('%Y%m%d')}.log"
        top = response["results"][0] if response["results"] else None
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "query": response["query"],
            "status": response["status"],
            "hits": len(response["results"]),
            "top_score": top["score"] if top else None,
            "top_source": top["source"] if top else None,
            "duration_ms": response["search"]["duration_ms"],
            "attempts": response["search"]["attempts"],
            "searches": response["search"]["searches"],
            "entity_hint_count": response["search"]["entity_hint_count"],
            "error": response["search"]["error"],
            "targets": sorted(set(network_log)),
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        log_path.chmod(0o600)
    except OSError:
        pass  # logging must never break recall itself


def _fake_search(
    query: str,
    limit: int,
    *,
    fallback: bool = False,
) -> tuple[list[dict[str, Any]] | None, str | None, list[str]]:
    del query
    fake_error = os.environ.get("RECALL_FAKE_ERROR")
    if fake_error:
        return None, f"MCP unreachable (simulated: {fake_error})", []
    rows_path = Path(os.environ["RECALL_FAKE_RESULTS"])
    payload: object = json.loads(rows_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("fallback" if fallback else "primary", [])
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        return None, "fake recall fixture is malformed", []
    rows = [dict(row) for row in payload]
    return rows[:limit], None, ["fake://recall-test"]


def _mcp_search(
    query: str, limit: int, picker: recall_pick.Picker | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None, list[str], str | None]:
    """Single-attempt search through the deployed W2-4 rag_ingest client."""
    runtime = Path(os.environ.get("RAG_INGEST_RUNTIME", _RUNTIME_DEFAULT)).expanduser()
    config_path = Path(os.environ.get("RECALL_CONFIG", _CONFIG_DEFAULT)).expanduser()
    sys.path.insert(0, str(runtime))
    try:
        from rag_ingest.config import load_config
        from rag_ingest.mcp_client import McpFatalError, McpMemoryClient, McpUnreachableError
    except ImportError as error:
        return None, f"recall runtime not available: {error.__class__.__name__}", [], None
    try:
        config = load_config(config_path)
    except Exception as error:  # ConfigError or malformed JSON — fail safe
        return None, f"recall config not available: {error.__class__.__name__}", [], None
    client = McpMemoryClient(base_url=config.mcp_base_url, api_key=config.api_key)
    try:
        if picker is None:
            rows = client.search_memory(query, limit=limit)
        else:
            rows, picked = picker(client, query, limit, McpFatalError)
            if picked:
                return rows, None, client.network_log + [_PICKED], config.mcp_base_url
    except (McpUnreachableError, McpFatalError) as error:
        # ONE attempt only — no retry loop. Mask detail down to the class name
        # plus a short reason so no auth material can leak into agent output.
        reason = str(error).split(":", 1)[0][:80]
        return None, f"{error.__class__.__name__}: {reason}", client.network_log, config.mcp_base_url
    return rows, None, client.network_log, config.mcp_base_url



def run_search(args: argparse.Namespace) -> int:
    started = time.monotonic()
    base_url: str | None = None
    use_fake = bool(
        os.environ.get("RECALL_FAKE_RESULTS") or os.environ.get("RECALL_FAKE_ERROR")
    )
    if use_fake:
        rows, error, network_log = _fake_search(args.query, args.limit)
        base_url = "fake://recall-test"
    else:
        rows, error, network_log, base_url = _mcp_search(args.query, args.limit, recall_pick.picker())
    picked = _PICKED in network_log

    searches = 1
    entity_hint_count = 0
    classification_query: str | None = None
    primary_has_hits = bool(
        rows is not None
        and error is None
        and recall_core.classify(
            args.query, rows, args.threshold, args.strong_threshold
        )
    )
    if (
        args.entity_fallback
        and not picked
        and error is None
        and rows is not None
        and not primary_has_hits
    ):
        intent = analyze_entity_intent(args.query)
        if intent.matches:
            searches = 2
            entity_hint_count = len(intent.entity_hints)
            fallback_query = " ".join(intent.entity_hints)
            if use_fake:
                auxiliary, auxiliary_error, auxiliary_log = _fake_search(
                    fallback_query, args.limit, fallback=True
                )
                auxiliary_base_url = "fake://recall-test"
            else:
                auxiliary, auxiliary_error, auxiliary_log, auxiliary_base_url = (
                    _mcp_search(fallback_query, args.limit)
                )
            network_log.extend(auxiliary_log)
            base_url = base_url or auxiliary_base_url
            error = auxiliary_error
            if auxiliary is None:
                rows = None
            else:
                rows = recall_core.merge_entity_rows(rows, auxiliary, intent.entity_hints)
                classification_query = fallback_query

    duration_ms = int((time.monotonic() - started) * 1000)
    response = recall_core.build_response(
        args.query,
        rows,
        error=error,
        base_url=base_url,
        limit=args.limit,
        duration_ms=duration_ms,
        threshold=args.threshold,
        strong_threshold=args.strong_threshold,
        classification_query=classification_query,
        searches=searches,
        entity_hint_count=entity_hint_count,
        picked=picked,
    )
    _log_line(response, network_log)
    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(recall_core.render_text(response))
    return 0


def run_evidence(args: argparse.Namespace) -> int:
    from automation.knowledge.facade import collect_evidence
    from automation.knowledge.pack import KnowledgeQuery
    from automation.knowledge.render import render_citations, render_verdict

    env = dict(os.environ)
    if args.entity_fallback:
        env["KNOWLEDGE_ENTITY_FALLBACK"] = "1"
    pack = collect_evidence(
        KnowledgeQuery(args.query, purpose=args.purpose, limit=args.limit, caller="recall"),
        env=env,
    )
    sources = render_citations(pack, "sources")
    if args.json:
        payload = {
            "version": pack.version,
            "query": pack.query.text,
            "verdict": pack.verdict,
            "evidence_count": len(pack.items),
            "layers": pack.layers,
            "notes": pack.notes,
            "sources": sources,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        verdict = render_verdict(pack)
        if verdict:
            print(verdict)
        print(sources)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recall_cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser("search", help="RAG search with source attribution")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--threshold", type=float, default=recall_core.DEFAULT_THRESHOLD)
    search.add_argument("--strong-threshold", type=float, default=recall_core.DEFAULT_STRONG_THRESHOLD)
    search.add_argument("--json", action="store_true", help="print raw recall-v1 JSON")
    search.add_argument("--entity-fallback", action="store_true", help="one entity-anchor fallback after no_memory")
    evidence = subparsers.add_parser("evidence", help="collect a knowledge-v1 evidence pack")
    evidence.add_argument("query")
    evidence.add_argument("--purpose", choices=("cite", "synthesize", "entity", "judgment"), default="cite")
    evidence.add_argument("--limit", type=int, default=8)
    evidence.add_argument("--json", action="store_true", help="print pack summary and rendered sources")
    evidence.add_argument("--entity-fallback", action="store_true", help="permit one entity-anchor fallback")
    args = parser.parse_args(argv)
    if not args.query.strip():
        parser.error("query must not be empty")
    return run_search(args) if args.command == "search" else run_evidence(args)


if __name__ == "__main__":
    sys.exit(main())

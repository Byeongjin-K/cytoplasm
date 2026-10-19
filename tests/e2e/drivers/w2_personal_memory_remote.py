"""W2-6 remote actuator — runs ON the agent account (<primary-node>), observes only.

Drives the deterministic W2 CLIs (meeting/wiki/rag_ingest/recall) through an
isolated per-run temp dir under the agent home. Emits one compact JSON object
(`OBS-JSON: {...}`) mapping case id -> observations; it never judges — the
local judge compares observations against the scenario YAML `expect` blocks.

Safety: no Discord call is possible (no notify channel, no team_channel_id,
no discord source block); production dirs/state are never written; the only
external effect is fixed-uuid5 synthetic vectors in personal_cha, removed by
the cleanup step of the same run (and self-healed by the next run).
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from os import environ
from pathlib import Path

HOME = Path.home()
# Governed skills live only at the read-only live mount since the 2026-08-15 skill-root
# inversion; ~/.hermes/skills is the agent's own self-skill root and holds no deployed copy.
# Same override name as automation.skill_mount.LIVE_ROOT_ENV (this file runs standalone).
SKILLS = Path(environ.get("AUTOPHAGY_SKILL_LIVE_ROOT", "").strip() or "/srv/autophagy-skills/live")
MEETING_CLI = str(SKILLS / "meeting" / "scripts" / "meeting_cli.py")
WIKI_CLI = str(SKILLS / "wiki" / "scripts" / "wiki_cli.py")
RECALL_CLI = str(SKILLS / "recall" / "scripts" / "recall_cli.py")
MAKE_PDF = str(SKILLS / "meeting" / "scripts" / "make_fixture_pdf.py")
RAG_RUNTIME = str(HOME / ".hermes" / "rag_ingest_runtime")
# The runtime package imports `automation.*` (group_roster); the production watcher puts the
# release tree right behind the runtime on sys.path (automation/rag_ingest/cron/rag_ingest_watch.py).
RELEASE_ROOT = "/srv/autophagy-agent-current"
WIKI_APPROVAL_CHANNEL = "999000000000000031"
PROD_RAG_CONFIG = HOME / ".hermes" / "rag-ingest" / "config.json"
MEETING_TOKEN = "w2e6-fixture-meeting-marker"
WIKI_TOKEN = "w2e6-fixture-wiki-marker"


def run(argv: list[str], env: dict[str, str], timeout: int = 240) -> tuple[int, str]:
    merged = {**environ, **env}
    completed = subprocess.run(
        argv, capture_output=True, timeout=timeout, cwd=HOME, env=merged, check=False
    )
    text = completed.stdout.decode("utf-8", errors="replace")
    text += completed.stderr.decode("utf-8", errors="replace")
    return completed.returncode, text


def parse_json(out: str) -> dict:
    """Parse the first JSON object embedded in mixed stdout/stderr text."""
    try:
        payload = json.JSONDecoder().raw_decode(out, out.index("{"))[0]
    except (ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def meeting_env(work: Path) -> dict[str, str]:
    config = work / "meeting-config.json"
    if not config.exists():
        config.write_text(
            json.dumps({"my_names": "cha,차", "agent_id": "agent-e2e-w2e6"}),
            encoding="utf-8",
        )
    return {
        "MEETING_CONFIG": str(config),
        "MEETING_NOTES_DIR": str(work / "notes" / "meetings"),
        "MEETING_LOG_DIR": str(work / "logs" / "meeting"),
    }


def meeting_log_records(work: Path) -> list[dict]:
    records = []
    for log in sorted((work / "logs" / "meeting").glob("ingest-*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            records.append(json.loads(line))
    return records


def rag_config(work: Path, base_url: str) -> Path:
    path = work / "rag-config.json"
    path.write_text(
        json.dumps(
            {
                "mcp_base_url": base_url,
                "secrets_file": str(HOME / ".env.secrets"),
                "api_key_env": "RAG_MCP_API_KEY",
                "state_dir": str(work / "rag-state"),
                "wiki_dir": str(work / "wiki"),
                "notes_dir": str(work / "notes"),
                "meetings_dir": str(work / "notes" / "meetings"),
                "max_chunk_chars": 1500,
                "perspective": {
                    "agent_id": "agent-e2e-w2e6",
                    "owner": "cha",
                    "role": "e2e-scenario-bank",
                    "project": "autophagy",
                    "interest_tags": "e2e,w2-6",
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def rag_run(config: Path, verbose: bool) -> tuple[int, str]:
    argv = [sys.executable, "-m", "rag_ingest", "run", "--config", str(config),
            "--sources", "wiki,meetings"]
    if verbose:
        argv.append("--verbose")
    return run(argv, {"PYTHONPATH": f"{RAG_RUNTIME}:{RELEASE_ROOT}"})


def recall(config: Path, work: Path, query: str) -> tuple[int, dict]:
    code, out = run(
        [sys.executable, RECALL_CLI, "search", query, "--json"],
        {"RECALL_CONFIG": str(config), "RECALL_LOG_DIR": str(work / "logs" / "recall")},
    )
    return code, parse_json(out)


def ingest_refusal_obs(work: Path, code: int, out: str, notice_key: str,
                       notice: str) -> dict:
    records = meeting_log_records(work)
    notes = list((work / "notes" / "meetings").glob("*.md"))
    return {
        "meeting_exit": code,
        notice_key: notice in out,
        "note_created": bool(notes),
        "cards_created": sum(len(r.get("card_ids", [])) for r in records),
        "refusal_logged": any(r.get("refused") for r in records),
    }


def case_happy_path(work: Path, fixtures: Path) -> dict:
    obs: dict = {}
    env = meeting_env(work)
    code, out = run(
        [sys.executable, MEETING_CLI, "ingest", "--file",
         str(fixtures / "meeting-happy.md"), "--recorded-response",
         str(fixtures / "recorded-llm-happy.json"), "--label", "w2e6-happy"],
        env,
    )
    obs["meeting_exit"] = code
    summary = parse_json(out) if code == 0 else {}
    obs["meeting_provider"] = summary.get("provider")
    # cbe00c40 (2026-09-04) renamed the flag when meeting moved to the shared Codex client.
    obs["codex_called"] = summary.get("codex_called")
    obs["team_posted"] = summary.get("team_posted")
    obs["cards_created"] = summary.get("cards")
    obs["milestones_in_minutes"] = summary.get("milestones")
    card_ids = [str(i) for r in meeting_log_records(work) for i in r.get("card_ids", [])]
    obs["card_ids_count"] = len(card_ids)
    code, out = run(["hermes", "kanban", "show", card_ids[0], "--json"], {}) \
        if card_ids else (1, "")
    obs["kanban_card_visible"] = code == 0 and MEETING_TOKEN in out
    notes = list((work / "notes/meetings").glob("*.md"))
    obs["minutes_has_milestone_token"] = any(MEETING_TOKEN in note.read_text(encoding="utf-8") for note in notes)

    wiki_env = {**env, "WIKI_ROOT": str(work / "wiki"),
                "WIKI_GATE_DIR": str(work / "wiki-gate"),
                "INTEROP_E2E_SECRET": secrets.token_hex(32)}
    body = (f"회의 요약 위키 (합성 E2E). 식별 마커: {WIKI_TOKEN}.\n"
            f"주간 미팅 위키 정리 — 초록 초안 일정과 데이터셋 사전 작업을 기록한다.\n")
    code, out = run(
        [sys.executable, WIKI_CLI, "draft", "--title", f"w2e6 회의 위키 {WIKI_TOKEN}",
         "--tags", "meeting,e2e", "--body", body], wiki_env)
    draft_id = next((word.split("=", 1)[1] for word in out.split()
                     if word.startswith("id=")), "")
    # An injected confirm consumes the binding persisted on the draft and never resolves
    # one (a13fcbcb); stamp it the way skills/wiki/scripts/scenario.sh does.
    run([sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import wiki_gate; "
         "d = wiki_gate.load_draft(sys.argv[2]); "
         "wiki_gate._write_json(wiki_gate._draft_path(d['id']), {**d, 'kind': 'wiki', "
         "'surface': 'owner-dm', 'channel_id': sys.argv[3], 'policy_version': 1})",
         str(SKILLS / "wiki" / "scripts"), draft_id, WIKI_APPROVAL_CHANNEL], wiki_env)
    injection = work / "injection.json"
    run([sys.executable, WIKI_CLI, "sign", "--draft", draft_id, "--out",
         str(injection), "--channel-id", WIKI_APPROVAL_CHANNEL], wiki_env)
    code, out = run([sys.executable, WIKI_CLI, "confirm", "--draft", draft_id,
                     "--injection-file", str(injection)], wiki_env)
    obs["wiki_saved"] = code == 0 and "SAVED" in out
    obs["wiki_method"] = "signed_injection_e2e" if "signed_injection_e2e" in out else ""
    obs["wiki_note_exists"] = bool(list((work / "wiki").glob("*.md")))

    base_url = json.loads(PROD_RAG_CONFIG.read_text(encoding="utf-8"))["mcp_base_url"]
    config = rag_config(work, base_url)
    code, out = rag_run(config, verbose=True)
    obs["rag_ingest_exit"] = code
    obs["rag_ingested_meeting"] = "INGESTED meeting:" in out
    obs["rag_ingested_wiki"] = "INGESTED wiki:" in out
    targets = [line.split(" ", 1)[1] for line in out.splitlines()
               if line.startswith("NETWORK ")]
    obs["rag_network_only_mcp"] = all(t.startswith(base_url) for t in targets)

    code, payload = recall(config, work, f"{MEETING_TOKEN} 중간보고서 제출")
    top = (payload.get("results") or [{}])[0]
    obs["recall_meeting_status"] = payload.get("status")
    obs["recall_meeting_source_is_meeting"] = str(top.get("source", "")).startswith("meeting:")
    obs["recall_meeting_attribution_present"] = bool(top.get("attribution"))
    obs["recall_meeting_top_score"] = top.get("score")
    code, payload = recall(config, work, f"{WIKI_TOKEN} 회의 요약 위키")
    top = (payload.get("results") or [{}])[0]
    obs["recall_wiki_status"] = payload.get("status")
    obs["recall_wiki_source_is_wiki"] = str(top.get("source", "")).startswith("wiki:")
    obs["recall_wiki_attribution_present"] = bool(top.get("attribution"))
    obs["recall_wiki_top_score"] = top.get("score")

    for note in list((work / "notes" / "meetings").glob("*.md")) + list(
            (work / "wiki").glob("*.md")):
        note.unlink()
    code, out = rag_run(config, verbose=True)
    obs["cleanup_vectors_removed"] = (
        code == 0 and "REMOVED meeting:" in out and "REMOVED wiki:" in out
    )
    ok = run(["hermes", "kanban", "archive", *card_ids], {})[0] == 0 if card_ids else False
    ok = ok and run(["hermes", "kanban", "archive", "--rm", *card_ids], {})[0] == 0
    obs["kanban_cleanup_ok"] = ok
    return obs


def case_empty_meeting(work: Path, fixtures: Path) -> dict:
    code, out = run(
        [sys.executable, MEETING_CLI, "ingest", "--file",
         str(fixtures / "meeting-empty.md")], meeting_env(work))
    return ingest_refusal_obs(work, code, out, "notice_empty_doc", "빈 문서")


def case_scanned_pdf(work: Path, fixtures: Path) -> dict:
    del fixtures
    pdf = work / "scanned.pdf"
    run([sys.executable, MAKE_PDF, str(pdf), "--scanned"], {})
    code, out = run(
        [sys.executable, MEETING_CLI, "ingest", "--file", str(pdf)], meeting_env(work))
    return ingest_refusal_obs(work, code, out, "notice_manual_conversion", "수동 변환 요청")


def case_rag_node_down(work: Path, fixtures: Path) -> dict:
    obs: dict = {}
    meetings = work / "notes" / "meetings"
    shutil.copy(fixtures / "meeting-happy.md", meetings / "w2e6-down.md")
    with socket.socket() as probe:  # bind+close => guaranteed-closed local port
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = rag_config(work, f"http://127.0.0.1:{port}")
    code, out = rag_run(config, verbose=False)
    obs["rag_ingest_exit"] = code
    obs["queued_notice"] = "job(s) queued" in out
    queue = work / "rag-state" / "queue.jsonl"
    obs["queue_jobs_persisted"] = queue.exists() and bool(
        queue.read_text(encoding="utf-8").strip())
    state = work / "rag-state" / "state.json"
    documents = json.loads(state.read_text(encoding="utf-8")).get("documents", {}) \
        if state.exists() else {}
    obs["state_not_advanced"] = not documents
    code, payload = recall(config, work, f"{MEETING_TOKEN} 중간보고서 제출")
    obs["recall_exit"] = code
    obs["recall_status"] = payload.get("status")
    obs["recall_message_unavailable"] = "검색 불가" in str(payload.get("message"))
    obs["recall_attempts"] = payload.get("search", {}).get("attempts")
    obs["recall_results_empty"] = payload.get("results") == []
    return obs


CASES = {
    "happy_path": case_happy_path,
    "empty_meeting": case_empty_meeting,
    "rag_node_down": case_rag_node_down,
    "scanned_pdf": case_scanned_pdf,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--cases", default=",".join(CASES))
    args = parser.parse_args()
    if environ.get("E2E_TEST_MODE") != "1":
        print("refusing: this actuator is E2E_TEST_MODE=1 only", file=sys.stderr)
        return 2
    observations: dict[str, dict] = {}
    (HOME / ".cache").mkdir(mode=0o700, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="w2e6-bank.", dir=HOME / ".cache"))
    try:
        for case_id in [c for c in args.cases.split(",") if c]:
            work = root / case_id
            for sub in ("notes/meetings", "state", "logs/meeting", "logs/recall",
                        "wiki", "wiki-gate", "rag-state"):
                (work / sub).mkdir(parents=True, exist_ok=True)
            try:
                observations[case_id] = CASES[case_id](work, args.fixtures)
                observations[case_id].setdefault("error", None)
            except Exception as error:  # observed, judged as mismatch locally
                observations[case_id] = {"error": f"{type(error).__name__}: {error}"}
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("OBS-JSON: " + json.dumps(observations, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

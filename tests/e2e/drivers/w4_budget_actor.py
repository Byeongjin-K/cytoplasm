"""W4-5 scenario actor: budget change -> request-mail draft -> approval gate ->
send (W4-3), GO branch.

Fully offline replica of the deployed W4-3 pipeline against sheet fixtures and
a stub gws binary in a throwaway temp dir. The request mail MUST pass through
the external-effect approval gate (W1-6 signed injection standing in for cha's
manual reaction, accepted only inside this runner's E2E env).

Emits one flat observation map per scenario case as `OBS-JSON: {...}`.
No network, no production paths, zero real sends.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

OWNER = "999900000000000625"
PEER_BOT = "111100000000000111"
CHANNEL = "999000000000000025"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RAW_AMOUNT = "137137"

GWS_STUB = """#!/bin/sh
{ printf '%s' "$*" | tr '\\n' ' '; printf '\\n'; } >> "$(dirname "$0")/gws-calls.log"
case "$*" in
  *"gmail +send"*) printf '{"id":"stub-mail-1","status":"sent"}\\n' ;;
esac
"""


def _fixture(path: Path, spent: str) -> None:
    rows = [["[과제비 운영 규칙]"], ["1. 오너 = 본인"], ["2. 갱신 주기 = 주 1회"],
            ["3. Sheet가 유일한 진실"], [],
            ["항목", "예산", "집행액", "잔액", "최종수정"],
            ["인건비", "0", "0", "0", "2026-07-14"],
            ["재료비", "0", spent, "0", "2026-07-14"],
            ["연구활동비", "0", "0", "0", "2026-07-14"]]
    path.write_text(json.dumps({"majorDimension": "ROWS", "range": "x", "values": rows}), encoding="utf-8")


def _setup(work: Path, root: Path) -> dict[str, str]:
    (work / "config.json").write_text('{"mail_to": "sandbox-selftest@example.invalid"}', encoding="utf-8")
    # The approvals channel arrives through the config key the shared directory
    # reads; AS-3.2 retired the per-flow *_APPROVALS_CHANNEL_ID env override.
    (work / "interop-config.json").write_text(
        f'{{"owner_id": "{OWNER}", "personal_approvals_channel_id": "{CHANNEL}"}}',
        encoding="utf-8",
    )
    gws = work / "gws-stub"
    gws.write_text(GWS_STUB, encoding="utf-8")
    gws.chmod(0o755)
    _fixture(work / "fixture-a.json", "0")
    _fixture(work / "fixture-b.json", RAW_AMOUNT)
    _fixture(work / "fixture-c.json", "200200")
    (work / "home").mkdir()
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(work / "home"),
        "BUDGET_DB": str(work / "budget.db"),
        "BUDGET_GATE_DIR": str(work / "gate"),
        "BUDGET_APPROVAL_LOG": str(work / "approvals.jsonl"),
        "BUDGET_CONFIG": str(work / "config.json"),
        "BUDGET_GWS_BIN": str(gws),
        "BUDGET_SHEET_FILE": str(work / "fixture-a.json"),
        "INTEROP_RUNTIME": str(root),
        "INTEROP_CONFIG": str(work / "interop-config.json"),
    }


def _cli(root: Path, env: dict[str, str], *args: str, e2e: dict[str, str] | None = None):
    cli = root / "skills" / "budget" / "scripts" / "budget_cli.py"
    return subprocess.run(  # noqa: S603
        [sys.executable, str(cli), *args],
        env={**env, **(e2e or {})}, capture_output=True, text=True, timeout=120, check=False,
    )


def _count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _schema_ok(record: dict[str, Any]) -> bool:
    approval = record.get("approval")
    return (
        isinstance(approval, dict)
        and approval.get("channel") == "approvals"
        and approval.get("method") == "signed_injection_e2e"
        and bool(TS_RE.match(str(record.get("timestamp"))))
        and bool(HASH_RE.match(str(record.get("hash"))))
        and isinstance(record.get("target_id"), str)
        and isinstance(record.get("result"), dict)
        and isinstance(record.get("action"), str)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    root = Path(parser.parse_args().root).resolve()
    sys.path.insert(0, str(root))
    from automation.interop.effect_approval import (
        ApprovalContext, ToolCall, validate_approval,
    )

    with tempfile.TemporaryDirectory(prefix="w4-budget-bank-") as tmp:
        work = Path(tmp)
        env = _setup(work, root)
        e2e = {"E2E_TEST_MODE": "1", "INTEROP_E2E_SECRET": secrets.token_hex(32)}
        approval_log = work / "approvals.jsonl"
        gws_calls = work / "gws-calls.log"
        target_id = "tool:gws_gmail_send:gws"
        obs: dict[str, dict[str, Any]] = {}

        # --- case 1: !budget query parity + baseline/no-change => zero effects -
        query = _cli(root, env, "query")
        item = _cli(root, env, "query", "--item", "재료비")
        missing = _cli(root, env, "query", "--item", "없는항목")
        baseline = _cli(root, env, "snapshot", "--no-post")
        unchanged = _cli(root, env, "snapshot", "--no-post")
        obs["query_snapshot"] = {
            "query_exit": query.returncode,
            "query_rows": len(re.findall(r"^ROW ", query.stdout, re.M)),
            "query_ok_marker": "BUDGET-OK n=3" in query.stdout,
            "item_filter_rows": len(re.findall(r"^ROW ", item.stdout, re.M)),
            "unknown_item_exit": missing.returncode,
            "baseline_created": "BASELINE" in baseline.stdout,
            "no_change_no_draft": "NO-CHANGE" in unchanged.stdout,
            "gws_calls": _count(gws_calls),
            "approval_records": _count(approval_log),
            "error": None,
        }

        # --- case 2: change -> masked draft, still zero mail -------------------
        env_b = {**env, "BUDGET_SHEET_FILE": str(work / "fixture-b.json")}
        drafted = _cli(root, env_b, "snapshot", "--no-post")
        match = re.search(r"DRAFT-CREATED id=([0-9a-f]+)", drafted.stdout)
        draft_id = match.group(1) if match else ""
        advanced = _cli(root, env_b, "snapshot", "--no-post")
        obs["draft_masked"] = {
            "draft_created": bool(draft_id),
            "draft_masked": "MASKED" in drafted.stdout,
            "raw_amount_leaked": RAW_AMOUNT in drafted.stdout,
            "snapshot_advanced": "NO-CHANGE" in advanced.stdout,
            "gws_calls": _count(gws_calls),
            "approval_records": _count(approval_log),
            "error": None,
        }

        # --- case 3: GO branch — signed owner approval -> gate -> 1 gws send ---
        # Offline signed approvals require the same persisted binding as production
        # (a13fcbcb: the injected confirm reads the channel stored on the record and
        # never resolves one). `--no-post` leaves none, so stamp it as mail's actor does.
        bind = subprocess.run(  # noqa: S603
            [
                sys.executable, "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import budget_gate; "
                "d = budget_gate.load_draft(sys.argv[3]); "
                "budget_gate.write_json(budget_gate.gate_dir() / 'drafts' / f\"{d['id']}.json\", "
                "{**d, 'kind': 'budget-mail', 'surface': 'skill-approvals', "
                "'channel_id': sys.argv[2], 'policy_version': 1})",
                str(root / "skills" / "budget" / "scripts"), CHANNEL, draft_id,
            ],
            env=env_b, capture_output=True, text=True, check=False,
        )
        assert bind.returncode == 0, bind.stderr
        _cli(root, env_b, "sign", "--draft", draft_id, "--out", str(work / "ok.json"), "--user-id", OWNER, e2e=e2e)
        confirm = _cli(root, env_b, "confirm", "--draft", draft_id, "--injection-file", str(work / "ok.json"), e2e=e2e)
        records = _records(approval_log)
        gate_recs = [r for r in records if r.get("action") == "external_effect.approval"]
        audit_recs = [r for r in records if r.get("action") == "budget.request_mail"]
        draft = json.loads((work / "gate" / "drafts" / f"{draft_id}.json").read_text(encoding="utf-8"))
        call = ToolCall("gws", {"command": shlex.join(draft["argv"])})
        ctx_e2e = ApprovalContext(approval_log=approval_log, owner_id=OWNER, e2e_test_mode=True)
        ctx_prod = ApprovalContext(approval_log=approval_log, owner_id=OWNER, e2e_test_mode=False)
        allowed = validate_approval(call, target_id, ctx_e2e)
        double = _cli(root, env_b, "confirm", "--draft", draft_id, "--injection-file", str(work / "ok.json"), e2e=e2e)
        send_log_text = (work / "gate" / "send-log.jsonl").read_text(encoding="utf-8")
        gws_text = gws_calls.read_text(encoding="utf-8") if gws_calls.exists() else ""
        obs["approved_send"] = {
            "confirm_exit": confirm.returncode,
            "sent_via_signed_injection": f"SENT draft={draft_id} method=signed_injection_e2e" in confirm.stdout,
            "gws_calls": _count(gws_calls),
            "send_argv_is_gmail_send": "gmail +send" in gws_text,
            "approval_records_total": len(records),
            "gate_approval_records": len(gate_recs),
            "audit_sent_records": sum(1 for r in audit_recs if r.get("result") == {"status": "sent"}),
            "approval_schema_valid": all(_schema_ok(r) for r in records),
            "approval_target_id": gate_recs[0]["target_id"] if gate_recs else "",
            "approval_owner_bound": bool(gate_recs) and gate_recs[0]["approval"].get("owner_id") == OWNER,
            "gate_hash_matches_record": bool(gate_recs) and allowed.action_hash == gate_recs[0]["hash"],
            "gate_allows_approved_hash_e2e": allowed.external_effect and allowed.allowed,
            "production_rejects_e2e_record": not validate_approval(call, target_id, ctx_prod).allowed,
            "double_confirm_rejected": double.returncode != 0,
            "sends_after_double_confirm": _count(gws_calls),
            "send_log_sent": '"status":"sent"' in send_log_text,
            "recipient_leaked_in_send_log": "sandbox-selftest@example.invalid" in send_log_text,
            "error": None,
        }

        # --- case 4: gate bypass — gws gmail +send with NO approval record -----
        bypass_call = ToolCall("gws", {"command": "gws gmail +send --to bypass@example.invalid --subject x --body y"})
        bypass = validate_approval(bypass_call, target_id, ctx_e2e)
        env_c = {**env, "BUDGET_SHEET_FILE": str(work / "fixture-c.json")}
        second = _cli(root, env_c, "snapshot", "--no-post")
        match2 = re.search(r"DRAFT-CREATED id=([0-9a-f]+)", second.stdout)
        draft2 = match2.group(1) if match2 else ""
        no_approval = _cli(root, env_c, "confirm", "--draft", draft2)
        obs["gate_bypass"] = {
            "bypass_is_external_effect": bypass.external_effect,
            "bypass_blocked": not bypass.allowed,
            "bypass_reason": bypass.reason,
            "second_draft_created": bool(draft2),
            "confirm_without_approval_rejected": no_approval.returncode != 0,
            "sends_after_bypass_attempts": _count(gws_calls),
            "approval_records_after_bypass": _count(approval_log),
            "error": None,
        }

        # --- case 5: peer-bot approval -> 0 send + rejection --------------------
        _cli(root, env_c, "sign", "--draft", draft2, "--out", str(work / "forged.json"), "--user-id", OWNER, "--forge-signature", e2e=e2e)
        forged = _cli(root, env_c, "confirm", "--draft", draft2, "--injection-file", str(work / "forged.json"), e2e=e2e)
        _cli(root, env_c, "sign", "--draft", draft2, "--out", str(work / "peer.json"), "--user-id", PEER_BOT, e2e=e2e)
        peer = _cli(root, env_c, "confirm", "--draft", draft2, "--injection-file", str(work / "peer.json"), e2e=e2e)
        discard = _cli(root, env_c, "discard", "--draft", draft2)
        obs["peer_bot_approval"] = {
            "forged_signature_rejected": forged.returncode != 0,
            "peer_bot_confirm_rejected": peer.returncode != 0,
            "sends_after_peer_attempts": _count(gws_calls),
            "approval_records_after_peer": _count(approval_log),
            "rejected_draft_discarded": "DISCARDED" in discard.stdout,
            "error": None,
        }

        print("OBS-JSON: " + json.dumps(obs, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

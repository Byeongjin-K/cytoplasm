"""W4-5 scenario actor: mail triage -> draft -> approval gate -> send (W4-2).

Fully offline replica of the deployed W4-2 pipeline against stub transports
(stub mailon repo/state.db, stub shared-model client, stub mailon-send binary,
stub calendar CLI) in a throwaway temp dir. mail-mode is pinned full-go (the
W0-7c verdict), so this exercises the GO branch: the auto-send path MUST pass
through the external-effect approval gate (W1-6 signed injection standing in
for cha's manual reaction, accepted only inside this runner's E2E env).

Emits one flat observation map per scenario case as `OBS-JSON: {...}`.
No network, no production paths, zero real sends.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import sqlite3
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

HERMES_STUB = """#!/usr/bin/env python3
import pathlib, re, sys
prompt = sys.argv[2] if len(sys.argv) > 2 else ""
base = pathlib.Path(__file__).resolve().parent
with (base / "codex-calls.log").open("a") as h:
    h.write("call\\n")
if '"category"' in prompt:
    if "beta-202" in prompt:
        category, reply, schedule = "spam", "false", "false"
    elif "delta-404" in prompt:
        category, reply, schedule = "important", "false", "true"
    elif "장비 사용" in prompt or "alpha-101" in prompt:
        category, reply, schedule = "important", "true", "false"
    else:
        category, reply, schedule = "normal", "false", "false"
    print('{"category": "%s", "reply_needed": %s, "schedule_needed": %s,'
          ' "budget": false, "schedule_text": "7월 20일 오후 3시 세미나", "reason": "stub"}' % (category, reply, schedule))
else:
    print('{"subject": "", "body": "요청하신 내용 확인했습니다. 감사합니다."}')
"""

SEND_STUB = """#!/bin/sh
base="$(dirname "$0")"
{ printf '%s' "$*" | tr '\\n' ' '; printf '\\n'; } >> "$base/mailon-send-calls.log"
printf '{"attachment_count": 0, "csrf_present": true, "network_post_count": 1, "status": "submitted"}\\n'
"""

CALENDAR_STUB = """import pathlib, sys
base = pathlib.Path(__file__).resolve().parent
with (base / "calendar-calls.log").open("a", encoding="utf-8") as h:
    h.write(" ".join(sys.argv[1:]) + "\\n")
print("CHANGE-SUMMARY stub")
print("DRAFT-CREATED id=calstub1 action=create sha256=deadbeef")
"""

FIXTURE_ROWS = [
    ("u-105", "세미나 안내 delta-404", "fixture-e@example.invalid", "2026-07-16T08:40:00", "f5.md", 5),
    ("u-104", "회신 요청: 장비 사용 확인", "fixture-d@example.invalid", "2026-07-16T08:35:00", "f4.md", 4),
    ("u-103", "W4-5 픽스처 제목 gamma-303", "fixture-c@example.invalid", "2026-07-16T08:30:00", "f3.md", 3),
    ("u-102", "[광고] 픽스처 스팸 beta-202", "ads@example.invalid", "2026-07-16T08:20:00", "f2.md", 2),
    ("u-101", "특허 출원 검토 요청 alpha-101", "fixture-a@example.invalid", "2026-07-16T08:10:00", "f1.md", 1),
]


def _stub(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _setup(work: Path, root: Path) -> tuple[dict[str, str], str]:
    repo = work / "repo"
    (repo / "mailon").mkdir(parents=True)
    (repo / "mailon" / "__init__.py").touch()
    (repo / "mailon" / "main.py").write_text("raise SystemExit(64)\n", encoding="utf-8")
    mails = repo / "data" / "mails" / "2026" / "07"
    mails.mkdir(parents=True)
    canary = f"PSEUDOSECRET-{secrets.token_hex(6)}"
    bodies = {
        "f1.md": f"특허 출원 명세서 초안 검토를 요청드립니다. 식별자: {canary}\n",
        "f2.md": "이번 주 한정 할인 광고입니다. 수신거부는 클릭.\n",
        "f3.md": "정기 소식지입니다. 조치가 필요하지 않습니다.\n",
        "f4.md": "장비 사용 일정 확인 후 회신 부탁드립니다. 기한: 7월 18일.\n",
        "f5.md": "7월 20일 오후 3시 세미나에 초대합니다.\n",
    }
    for name, body in bodies.items():
        (mails / name).write_text(body, encoding="utf-8")
    conn = sqlite3.connect(repo / "data" / "state.db")
    conn.execute(
        "CREATE TABLE messages (uid TEXT PRIMARY KEY, folder TEXT NOT NULL DEFAULT 'inbox',"
        " subject TEXT, sender TEXT, recv_date TEXT, markdown_path TEXT, saved_at INTEGER NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
        [(u, "inbox", s, f, d, f"data/mails/2026/07/{m}", n) for u, s, f, d, m, n in FIXTURE_ROWS],
    )
    conn.commit()
    conn.close()
    (work / "env.secrets").write_text(
        "MAILON_ID=DUMMY-id\nMAILON_PW=DUMMY-pw\nMAILON_TOTP_SECRET=DUMMYBASE32\n", encoding="utf-8"
    )
    (work / "mail-mode-repo.json").write_text(
        '{"mode": "full-go", "decided_at": "2026-07-16T00:00:00Z", "source": "W0-7c"}', encoding="utf-8"
    )
    # The approvals channel arrives through the config key the shared directory
    # reads; AS-3.2 retired the per-flow *_APPROVALS_CHANNEL_ID env override.
    (work / "interop-config.json").write_text(
        f'{{"owner_id": "{OWNER}", "personal_approvals_channel_id": "{CHANNEL}"}}',
        encoding="utf-8",
    )
    _stub(work / "hermes-stub", HERMES_STUB)
    _stub(work / "mailon-send-stub", SEND_STUB)
    (work / "calendar-stub.py").write_text(CALENDAR_STUB, encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(work / "home"),
        "MAIL_WRAPPER_REPO": str(repo),
        "MAIL_WRAPPER_PYTHON": sys.executable,
        "MAIL_WRAPPER_ENV_FILE": str(work / "env.secrets"),
        "TRIAGE_GATE_DIR": str(work / "triage-gate"),
        "TRIAGE_DB": str(work / "triage.db"),
        "TRIAGE_APPROVAL_LOG": str(work / "approvals.jsonl"),
        "TRIAGE_MAIL_HOME": str(work / "mailhome"),
        "TRIAGE_LLM_LOG": str(work / "llm-calls.jsonl"),
        "TRIAGE_MAIL_MODE_FILE": str(work / "runtime" / "mail-mode.json"),
        "TRIAGE_MAIL_MODE_REPO": str(work / "mail-mode-repo.json"),
        "TRIAGE_MAILON_PYTHON": str(work / "mailon-send-stub"),
        "AUTOPHAGY_HERMES_BIN": str(work / "hermes-stub"),
        "TRIAGE_CALENDAR_CLI": str(work / "calendar-stub.py"),
        "INTEROP_RUNTIME": str(root),
        "INTEROP_CONFIG": str(work / "interop-config.json"),
        # This checkout copy is the governed one for the run (mail_runtime.governed_copy_refusal).
        "AUTOPHAGY_SKILL_LIVE_ROOT": str(root / "skills"),
    }
    (work / "home").mkdir()
    return env, canary


def _cli(root: Path, env: dict[str, str], *args: str, e2e: dict[str, str] | None = None):
    cli = root / "skills" / "mail" / "scripts" / "triage_cli.py"
    return subprocess.run(  # noqa: S603
        [sys.executable, str(cli), *args],
        env={**env, **(e2e or {})}, capture_output=True, text=True, timeout=300, check=False,
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
        ApprovalBinding, ApprovalContext, SignedApprovalEvent, ToolCall,
        approval_challenge, validate_approval, record_signed_e2e_approval,
    )
    from automation.interop.injection_adapter import InboundEvent, sign_event

    with tempfile.TemporaryDirectory(prefix="w4-mail-bank-") as tmp:
        work = Path(tmp)
        env, canary = _setup(work, root)
        e2e_secret = secrets.token_hex(32)
        e2e = {"E2E_TEST_MODE": "1", "INTEROP_E2E_SECRET": e2e_secret}
        approval_log = work / "approvals.jsonl"
        send_log = work / "mailon-send-calls.log"
        target_id = "tool:mailon_send:python3"
        obs: dict[str, dict[str, Any]] = {}

        # --- case 1: triage tick -> drafts only, common routing, 0 send ----
        t1 = _cli(root, env, "process", "--no-sync", "--no-post")
        t2 = _cli(root, env, "process", "--no-sync", "--no-post")
        obs["triage_draft"] = {
            "process_exit": t1.returncode,
            "processed_n": 5 if "PROCESSED n=5" in t1.stdout else -1,
            "drafts_created": len(re.findall(r"action=draft:[0-9a-f]+", t1.stdout)),
            "spam_skipped": "action=spam-skip" in t1.stdout,
            "normal_no_action": "action=no-action" in t1.stdout,
            "calendar_delegated": "action=calendar:calstub1" in t1.stdout,
            "keyword_draft_created": "category=important action=draft:" in t1.stdout,
            "model_calls": _count(work / "codex-calls.log"),
            "canary_on_approvals_surface": canary in t1.stdout,
            "sends_before_approval": _count(send_log),
            "approval_records_before_approval": _count(approval_log),
            "reprocess_idempotent": "PROCESSED n=0" in t2.stdout,
            "error": None,
        }

        drafts = [json.loads(p.read_text(encoding="utf-8"))
                  for p in (work / "triage-gate" / "drafts").glob("*.json")]
        pub_id = next((d["id"] for d in drafts if d["uid"] == "u-104"), "")
        sens_id = next((d["id"] for d in drafts if d["uid"] == "u-101"), "")
        # Offline signed approvals require the same persisted binding as production.
        bind = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import triage_gate; "
                "[(triage_gate.set_approval_binding(triage_gate.load_draft(d), "
                "kind='reply', surface='skill-approvals', channel_id=sys.argv[2], "
                "policy_version=1)) for d in sys.argv[3:]]",
                str(root / "skills/mail/scripts"), CHANNEL, pub_id, sens_id,
            ],
            env=env, capture_output=True, text=True, check=False,
        )
        assert bind.returncode == 0, bind.stderr

        # --- case 2: GO branch — signed owner approval -> gate -> 1 send -------
        _cli(root, env, "sign", "--draft", pub_id, "--out", str(work / "ok.json"), "--user-id", OWNER, e2e=e2e)
        confirm = _cli(root, env, "confirm", "--draft", pub_id, "--injection-file", str(work / "ok.json"), e2e=e2e)
        assert confirm.returncode == 0, confirm.stderr
        records = _records(approval_log)
        gate_recs = [r for r in records if r.get("action") == "external_effect.approval"]
        audit_recs = [r for r in records if r.get("action") == "mail.reply_send"]
        draft = json.loads((work / "triage-gate" / "drafts" / f"{pub_id}.json").read_text(encoding="utf-8"))
        call = ToolCall("python3", {"command": shlex.join(draft["argv"])})
        ctx_e2e = ApprovalContext(approval_log=approval_log, owner_id=OWNER, e2e_test_mode=True)
        ctx_prod = ApprovalContext(approval_log=approval_log, owner_id=OWNER, e2e_test_mode=False)
        allowed = validate_approval(call, target_id, ctx_e2e)
        double = _cli(root, env, "confirm", "--draft", pub_id, "--injection-file", str(work / "ok.json"), e2e=e2e)
        send_log_text = (work / "triage-gate" / "send-log.jsonl").read_text(encoding="utf-8")
        obs["approved_send"] = {
            "confirm_exit": confirm.returncode,
            "sent_via_signed_injection": f"SENT draft={pub_id} method=signed_injection_e2e" in confirm.stdout,
            "mailon_send_calls": _count(send_log),
            "send_argv_is_mailon_send": "mailon.main send" in (send_log.read_text(encoding="utf-8") if send_log.exists() else ""),
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
            "sends_after_double_confirm": _count(send_log),
            "send_log_sent": '"status":"sent"' in send_log_text,
            "recipient_leaked_in_send_log": "example.invalid" in send_log_text,
            "error": None,
        }

        # --- case 3: gate bypass — external effect with NO approval record -----
        bypass_call = ToolCall("python3", {"command": "python3 -m mailon.main send --to bypass@example.invalid --subject x --body y"})
        bypass = validate_approval(bypass_call, target_id, ctx_e2e)
        no_approval = _cli(root, env, "confirm", "--draft", sens_id)
        watch = _cli(root, env, "watch", e2e=e2e)
        obs["gate_bypass"] = {
            "bypass_is_external_effect": bypass.external_effect,
            "bypass_blocked": not bypass.allowed,
            "bypass_reason": bypass.reason,
            "confirm_without_approval_rejected": no_approval.returncode != 0,
            "sends_after_bypass_attempts": _count(send_log),
            "approval_records_after_bypass": _count(approval_log),
            "watch_refuses_e2e_env": watch.returncode == 3,
            "error": None,
        }

        # --- case 4: peer-bot approval reaction -> 0 send + rejection ----------
        _cli(root, env, "sign", "--draft", sens_id, "--out", str(work / "forged.json"), "--user-id", OWNER, "--forge-signature", e2e=e2e)
        forged = _cli(root, env, "confirm", "--draft", sens_id, "--injection-file", str(work / "forged.json"), e2e=e2e)
        _cli(root, env, "sign", "--draft", sens_id, "--out", str(work / "peer.json"), "--user-id", PEER_BOT, e2e=e2e)
        peer = _cli(root, env, "confirm", "--draft", sens_id, "--injection-file", str(work / "peer.json"), e2e=e2e)
        peer_event = InboundEvent(
            event_id="w4-5-peer-bot", user_id=PEER_BOT, channel_id="approvals",
            text=approval_challenge(bypass.action_hash, bypass.target_id),
        )
        peer_recorded = record_signed_e2e_approval(
            ctx_e2e, ApprovalBinding(bypass.action_hash, bypass.target_id),
            SignedApprovalEvent(peer_event, sign_event(peer_event, e2e_secret.encode()), e2e_secret.encode()),
        )
        discard = _cli(root, env, "discard", "--draft", sens_id)
        obs["peer_bot_approval"] = {
            "forged_signature_rejected": forged.returncode != 0,
            "peer_bot_confirm_rejected": peer.returncode != 0,
            "peer_bot_gate_record_refused": not peer_recorded,
            "sends_after_peer_attempts": _count(send_log),
            "approval_records_after_peer": _count(approval_log),
            "rejected_draft_discarded": "DISCARDED" in discard.stdout,
            "error": None,
        }

        print("OBS-JSON: " + json.dumps(obs, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

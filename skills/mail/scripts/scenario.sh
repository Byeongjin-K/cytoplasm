#!/usr/bin/env bash
# Sandbox scenario for the mail skill (W1-8 pipeline stage 1 / post-mount smoke).
# Fully offline: a stub mailon package reproduces the cached stdout/exit-code
# contract (sync ok / auth fail 2 / structural crash 3) against a synthetic
# state.db. Proves: list/get/classify JSON shapes, masking, the re-auth
# guidance path, the exit-3 local fallback, the READ-ONLY command guard, and
# the env allowlist (DISCORD_BOT_TOKEN never reaches mailon). No network, no
# real secrets, no real mail data.
#
# W4-2 triage legs (stub LLMs / stub mailon-send / stub calendar): mail processing
# all text uses the account-configured shared model chain, sanitized
# #approvals rendering, claim idempotency, fail-closed confirm, signed-inject
# send with approval records, discard, 2-consecutive-fail NO-GO downgrade +
# W4-1N switch record, and the watch E2E/mode refusals.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY-* ]] || fail "secret does not carry the DUMMY- prefix (real secrets forbidden in sandbox)"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../.." && pwd)"
# The copy under test IS the governed one for this run: on a node the real mount
# (/srv/autophagy-skills/live/mail) exists beside this sandbox copy, and
# mail_runtime.governed_copy_refusal would otherwise refuse every mutating leg.
export AUTOPHAGY_SKILL_LIVE_ROOT="$(cd "$script_dir/../.." && pwd)"
work="$(mktemp -d)"
trap 'cd / && rm -rf "$work"' EXIT
cd "$work"  # sandbox cwd may be unreadable to this account

canary="PSEUDOSECRET-$(python3 -c 'import secrets; print(secrets.token_hex(6))')"

# --- stub mailon repo --------------------------------------------------------
mkdir -p "$work/repo/mailon" "$work/repo/data/mails/2026/07"
: > "$work/repo/mailon/__init__.py"
cat > "$work/repo/mailon/main.py" <<'PY'
import pathlib, sys
if "DISCORD_BOT_TOKEN" in __import__("os").environ:
    print("ENV-LEAK: non-allowlisted secret reached mailon", file=sys.stderr)
    sys.exit(99)
mode_file = pathlib.Path("stub_mode.txt")
mode = mode_file.read_text().strip() if mode_file.is_file() else "ok"
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "sync":
    if mode == "ok":
        print("2026-07-16 09:00:00 [INFO] mailon: 0 already-saved uids will be skipped")
        print("OK: 2 new mail(s) (retries: 0 recovered, 0 still failing)")
        sys.exit(0)
    if mode == "auth_fail":
        print("FAIL: LoginError: login flow did not reach the mailbox", file=sys.stderr)
        sys.exit(2)
    if mode == "crash3":
        print("FAIL: RuntimeError: inbox folderUid selector not found", file=sys.stderr)
        sys.exit(3)
if cmd == "status":
    print("Saved mails: 3")
    print("Last run #6: status=ok new=2 started=2026-07-16T09:00:00 finished=2026-07-16T09:01:00 ")
    sys.exit(0)
if cmd == "resolve":
    import json
    name = sys.argv[sys.argv.index("--name") + 1] if "--name" in sys.argv else ""
    print(json.dumps({"status": "ok", "query": name, "candidates": [
        {"group": "organization", "name": "김샘플",
         "email": "ksample@example.invalid", "org": "AX융합연구센터"},
        {"group": "contacts", "name": "김샘플",
         "email": "ksample@example.invalid", "org": ""},
    ], "post_count": 1}, ensure_ascii=False))
    sys.exit(0)
print("stub: unexpected invocation", file=sys.stderr)
sys.exit(64)
PY

python3 - "$work/repo/data/state.db" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute("""CREATE TABLE messages (
    uid TEXT PRIMARY KEY, folder TEXT NOT NULL DEFAULT 'inbox', subject TEXT,
    sender TEXT, recv_date TEXT, markdown_path TEXT, saved_at INTEGER NOT NULL)""")
rows = [
    ("u-105", "inbox", "세미나 안내 delta-404", "fixture-e@example.invalid",
     "2026-07-16T08:40:00", "data/mails/2026/07/f5.md", 5),
    ("u-104", "inbox", "회신 요청: 장비 사용 확인", "fixture-d@example.invalid",
     "2026-07-16T08:35:00", "data/mails/2026/07/f4.md", 4),
    ("u-103", "inbox", "W4-1 픽스처 제목 gamma-303", "fixture-c@example.invalid",
     "2026-07-16T08:30:00", "data/mails/2026/07/f3.md", 3),
    ("u-102", "inbox", "[광고] 픽스처 스팸 beta-202", "ads@example.invalid",
     "2026-07-16T08:20:00", "data/mails/2026/07/f2.md", 2),
    ("u-101", "inbox", "특허 출원 검토 요청 alpha-101", "fixture-a@example.invalid",
     "2026-07-16T08:10:00", "data/mails/2026/07/f1.md", 1),
]
conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows)
conn.commit()
PY
printf '특허 출원 명세서 초안 검토를 요청드립니다. 식별자: %s\n' "$canary" > "$work/repo/data/mails/2026/07/f1.md"
printf '이번 주 한정 할인 광고입니다. 수신거부는 클릭.\n' > "$work/repo/data/mails/2026/07/f2.md"
printf '정기 소식지입니다. 조치가 필요하지 않습니다.\n' > "$work/repo/data/mails/2026/07/f3.md"
printf '장비 사용 일정 확인 후 회신 부탁드립니다. 기한: 7월 18일.\n' > "$work/repo/data/mails/2026/07/f4.md"
printf '7월 20일 오후 3시 세미나에 초대합니다.\n' > "$work/repo/data/mails/2026/07/f5.md"

cat > "$work/env.secrets" <<EOF
MAILON_ID=DUMMY-id
MAILON_PW=DUMMY-pw
MAILON_TOTP_SECRET=DUMMYBASE32
DISCORD_BOT_TOKEN=DUMMY-not-for-mailon
EOF

export MAIL_WRAPPER_REPO="$work/repo"
export MAIL_WRAPPER_PYTHON="$(command -v python3)"
export MAIL_WRAPPER_ENV_FILE="$work/env.secrets"
wrap() { python3 "$script_dir/mail_wrapper.py" "$@"; }

# --- 1) list (local, unmasked) + get + classify shapes ------------------------
wrap list --limit 5 > "$work/list.out" || fail "local list exited non-zero"
wrap get u-101 > "$work/get.out" || fail "get exited non-zero"
wrap classify --uid u-101 > "$work/cls-patent.out" || fail "classify uid failed"
wrap classify --subject "[광고] 아무거나" --sender "x@example.invalid" \
  > "$work/cls-spam.out" || fail "classify spam failed"
python3 - "$work/list.out" "$work/get.out" "$work/cls-patent.out" "$work/cls-spam.out" <<'PY' \
  || fail "shape assertions failed"
import json, sys
lst = json.load(open(sys.argv[1]))
assert lst["wrapper"] == "mail-wrapper-v1" and lst["status"] == "ok"
assert lst["count"] == 5 and lst["synced"] is False and lst["sync"] is None
assert [m["uid"] for m in lst["mails"]][:3] == ["u-105", "u-104", "u-103"], "recv_date DESC order"
assert set(lst["mails"][0]) == {"uid", "folder", "date", "subject", "sender", "markdown_path"}
got = json.load(open(sys.argv[2]))
assert got["mail"]["uid"] == "u-101" and "특허" in got["mail"]["subject"]
pat = json.load(open(sys.argv[3]))["classification"]
assert pat["category"] == "important" and "patent_sensitive" not in pat["flags"]
assert "route" not in pat and pat["basis"] == "metadata-only"
spam = json.load(open(sys.argv[4]))["classification"]
assert spam["category"] == "spam" and "route" not in spam
PY

# --- 2) masking: no fixture plaintext leaves the wrapper ----------------------
wrap list --limit 5 --masked > "$work/masked.out" || fail "masked list failed"
python3 - "$work/masked.out" <<'PY' || fail "masking assertions failed"
import json, re, sys
raw = open(sys.argv[1], encoding="utf-8").read()
assert "픽스처" not in raw and "특허" not in raw and "example.invalid" not in raw
for m in json.loads(raw)["mails"]:
    assert re.fullmatch(r"sha256:[0-9a-f]{16}", m["subject"]), m["subject"]
    assert re.fullmatch(r"sha256:[0-9a-f]{16}", m["sender"])
PY

# --- 3) live paths against the stub: ok / auth fail / crash-3 fallback --------
printf 'ok' > "$work/repo/stub_mode.txt"
wrap list --limit 5 --sync > "$work/sync-ok.out" || fail "sync-ok list failed"
printf 'auth_fail' > "$work/repo/stub_mode.txt"
rc=0; wrap list --sync > "$work/auth.out" || rc=$?
[[ "$rc" == 2 ]] || fail "auth-fail path must exit 2 (got $rc)"
printf 'crash3' > "$work/repo/stub_mode.txt"
wrap list --sync > "$work/crash3.out" || fail "crash-3 must fall back to local read (exit 0)"
wrap status > "$work/status.out" || fail "status failed"
python3 - "$work/sync-ok.out" "$work/auth.out" "$work/crash3.out" "$work/status.out" <<'PY' \
  || fail "live-path assertions failed"
import json, sys
ok = json.load(open(sys.argv[1]))
assert ok["sync"] == {"exit_code": 0, "meaning": "ok", "new_mails": 2}, ok["sync"]
auth = json.load(open(sys.argv[2]))
assert auth["status"] == "error" and auth["error_code"] == "auth_error"
assert auth["mailon_exit_code"] == 2 and "재인증" in auth["guidance"]
assert "LoginError" not in json.dumps(auth) or auth["failure_signature"] == "login_error"
crash = json.load(open(sys.argv[3]))
assert crash["status"] == "ok" and crash["sync"]["exit_code"] == 3
assert crash["sync"]["fallback"] == "local-state-db"
assert crash["sync"]["failure_signature"] == "inbox_folder_uid_selector"
status = json.load(open(sys.argv[4]))
assert status["saved_mails"] == 3 and status["last_run"]["run_id"] == 6
PY

# --- 4) READ-ONLY guard + not-found exit codes --------------------------------
python3 - "$script_dir" <<'PY' || fail "read-only guard assertions failed"
import sys
sys.path.insert(0, sys.argv[1])
import mail_wrapper
try:
    mail_wrapper.run_mailon(mail_wrapper._cfg(), ["send", "--to", "x"])
except ValueError:
    pass
else:
    raise AssertionError("send must be refused by the stage-1 READ-ONLY guard")
assert mail_wrapper.MAILON_INTERFACE["collection"]["page_size"] == 20
PY
rc=0; wrap get u-999 > /dev/null || rc=$?
[[ "$rc" == 5 ]] || fail "get missing uid must exit 5 (got $rc)"

# --- 4b) resolve: name→email autocomplete (read-only) -------------------------
printf 'ok' > "$work/repo/stub_mode.txt"
wrap resolve --name 김샘플 > "$work/resolve.out" || fail "resolve failed"
wrap resolve --name 김샘플 --masked > "$work/resolve-masked.out" || fail "masked resolve failed"
python3 - "$work/resolve.out" "$work/resolve-masked.out" <<'PY' || fail "resolve assertions failed"
import json, re, sys
res = json.load(open(sys.argv[1]))
assert res["wrapper"] == "mail-wrapper-v1" and res["command"] == "resolve"
assert res["status"] == "ok" and res["masked"] is False
assert res["candidate_count"] == 2
assert res["candidates"][0]["email"] == "ksample@example.invalid"
assert res["candidates"][0]["group"] == "organization"
raw = open(sys.argv[2], encoding="utf-8").read()
assert "김샘플" not in raw and "example.invalid" not in raw and "AX융합연구센터" not in raw
assert "sha256:" in raw
masked = json.loads(raw)
assert masked["masked"] is True
for cand in masked["candidates"]:
    assert re.fullmatch(r"sha256:[0-9a-f]{16}", cand["name"]), cand
    assert re.fullmatch(r"sha256:[0-9a-f]{16}", cand["email"]), cand
assert [c["group"] for c in masked["candidates"]] == ["organization", "contacts"]
PY

# ==============================================================================
# W4-2 triage pipeline legs
# ==============================================================================
export TRIAGE_GATE_DIR="$work/triage-gate" TRIAGE_DB="$work/triage.db"
export TRIAGE_APPROVAL_LOG="$work/triage-approvals.jsonl"
export TRIAGE_MAIL_HOME="$work/mailhome" TRIAGE_LLM_LOG="$work/llm-calls.jsonl"
export TRIAGE_MAIL_MODE_FILE="$work/runtime/mail-mode.json"
export TRIAGE_MAIL_MODE_REPO="$work/mail-mode-repo.json"
mkdir -p "$work/runtime"
export SCENARIO_APPROVAL_CHANNEL_ID="100000000000000001"
export TRIAGE_MAILON_PYTHON="$work/mailon-send-stub"
export AUTOPHAGY_HERMES_BIN="$work/hermes-stub"  # 공유 Codex 클라이언트의 유일한 훅
export TRIAGE_CALENDAR_CLI="$work/calendar-stub.py"
printf '{"mode": "full-go", "decided_at": "2026-07-16T00:00:00Z", "source": "W0-7c"}' \
  > "$TRIAGE_MAIL_MODE_REPO"

cat > "$work/hermes-stub" <<'PY'
#!/usr/bin/env python3
import json, pathlib, re, sys
# 공유 클라이언트 argv: [bin, -z, PROMPT, --provider, openai-codex, ...]
prompt = sys.argv[sys.argv.index("-z") + 1] if "-z" in sys.argv[:-1] else ""
base = pathlib.Path(__file__).resolve().parent
with (base / "codex-calls.log").open("a", encoding="utf-8") as h:
    h.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
with (base / "codex-inputs.log").open("a", encoding="utf-8") as h:
    h.write(prompt + "\n===\n")
if '"category"' in prompt:
    # NOTE: match on UNIQUE fixture tokens, not generic words — the classify
    # instructions themselves contain 광고/회신/세미나 and would false-hit.
    if "beta-202" in prompt:
        print('{"category": "spam", "reply_needed": false, "schedule_needed": false,'
              ' "budget": false, "schedule_text": "", "reason": "stub"}')
    elif "장비 사용" in prompt:
        print('{"category": "important", "reply_needed": true, "schedule_needed": false,'
              ' "budget": false, "schedule_text": "", "reason": "stub"}')
    elif "delta-404" in prompt:
        print('{"category": "important", "reply_needed": false, "schedule_needed": true,'
              ' "budget": false, "schedule_text": "7월 20일 오후 3시 세미나", "reason": "stub"}')
    elif "alpha-101" in prompt:  # 특허 본문도 같은 처리 경로를 쓴다
        print('{"category": "important", "reply_needed": true, "schedule_needed": false,'
              ' "budget": false, "schedule_text": "", "reason": "stub"}')
    else:
        print('{"category": "normal", "reply_needed": false, "schedule_needed": false,'
              ' "budget": false, "schedule_text": "", "reason": "stub"}')
else:
    match = re.search(r"PSEUDOSECRET-[0-9a-f]+", prompt)
    extra = f" 참조: {match.group(0)}." if match else ""
    print('{"subject": "", "body": "요청하신 내용 확인했습니다.%s 감사합니다."}' % extra)
PY
cat > "$work/mailon-send-stub" <<'SH'
#!/bin/sh
base="$(dirname "$0")"
{ printf '%s' "$*" | tr '\n' ' '; printf '\n'; } >> "$base/mailon-send-calls.log"
mode=ok
[ -f "$base/stub_send_mode.txt" ] && mode="$(cat "$base/stub_send_mode.txt")"
if [ "$mode" = ok ]; then
  printf '{"attachment_count": 0, "csrf_present": true, "network_post_count": 1, "status": "submitted"}\n'
elif [ "$mode" = compose ]; then
  printf '{"status": "error", "error_code": "external_service_error", "stage": "provider", "retryable": true, "cause": "compose_not_ready", "step": "compose_open", "browser_command": "eval", "error_kind": "js_type_error", "message": "x"}\n'
  exit 2
else
  printf '{"status": "error", "error_code": "send_failed"}\n' >&2
  exit 1
fi
SH
cat > "$work/calendar-stub.py" <<'PY'
import pathlib, sys
base = pathlib.Path(__file__).resolve().parent
with (base / "calendar-calls.log").open("a", encoding="utf-8") as h:
    h.write(" ".join(sys.argv[1:]) + "\n")
print("CHANGE-SUMMARY stub")
print("DRAFT-CREATED id=calstub1 action=create sha256=deadbeef")
PY
chmod +x "$work/hermes-stub" "$work/mailon-send-stub"
tri() { python3 "$script_dir/triage_cli.py" "$@"; }
send_calls() { [ -f "$work/mailon-send-calls.log" ] && wc -l < "$work/mailon-send-calls.log" || echo 0; }
cat > "$work/evidence-pack.json" <<'JSON'
{"version":"knowledge-v1","query":{"text":"peer@example.invalid 일정","purpose":"synthesize","sources":["rag","wiki","twin"],"tags":[],"limit":8,"caller":"mail"},"verdict":"hit","items":[{"id":"E1","store":"rag","source_type":"note","ref":"contacts/peer.md","title":"상대 노트","doc_date":"2026-08-18","date_basis":"path","score":0.9,"grounded":true,"authority":null,"expired":null,"sensitivity":null,"content":"지난 일정 합의","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],"layers":{"rag":"hit","wiki":"none","twin":"none"},"notes":[]}
JSON
if python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import automation.entity_preflight.gate; import automation.knowledge.render' \
  "$repo_root" 2>/dev/null; then
  AUTOPHAGY_REPO_ROOT="$repo_root" KNOWLEDGE_FAKE_PACK="$work/evidence-pack.json" \
    tri evidence --counterparty peer@example.invalid --subject 일정 --json \
    | grep -q '"evidence_count": 1' || fail "offline evidence preview"
else
  AUTOPHAGY_REPO_ROOT="$repo_root" KNOWLEDGE_FAKE_PACK="$work/evidence-pack.json" \
    tri evidence --counterparty peer@example.invalid --subject 일정 \
    | grep -q '근거 수집 불가' || fail "offline evidence degradation"
fi

# --- 5) triage happy tick: common route, drafts, no send ----------
tri process --no-sync --no-post > "$work/t1.out" || fail "triage process failed"
grep -q '^PROCESSED n=5' "$work/t1.out" || fail "expected 5 processed mails"
grep -q 'action=spam-skip' "$work/t1.out" || fail "spam mail not skipped"
grep -q 'action=no-action' "$work/t1.out" || fail "normal mail not no-action"
grep -q 'action=calendar:calstub1' "$work/t1.out" || fail "schedule mail not delegated to calendar"
grep -Ec 'action=draft:[0-9a-f]+' "$work/t1.out" | grep -qx 2 || fail "expected exactly 2 drafts"
grep -q 'category=important action=draft:' "$work/t1.out" \
  || fail "keyword mail did not produce a draft"
grep -q "$canary" "$work/t1.out" || fail "keyword body missing from approval preview"
[[ "$(wc -l < "$work/codex-calls.log")" == 7 ]] \
  || fail "expected exactly 7 codex calls (5 classify + 2 drafts)"
grep -q '"provider":"openai-codex"' "$work/llm-calls.jsonl" || fail "routing log missing codex entries"
python3 - "$work/codex-calls.log" "$work/llm-calls.jsonl" <<'PY' || fail "codex routing assertions failed"
import json, sys

# 모든 호출은 모델을 고르지 않고 계정 설정(주 모델 + fallback_providers)을 따른다 —
# --provider/-m 이 붙으면 설정을 덮고, --ignore-user-config 가 붙으면 폴백 체인이 꺼진다.
for line in open(sys.argv[1], encoding="utf-8"):
    argv = json.loads(line)
    assert not {"--ignore-user-config", "--provider", "-m"} & set(argv), argv

# 감사 로그에는 승인 티어만 남고, 강등 표식은 존재조차 하지 않는다.
records = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert records, "routing log empty"
for rec in records:
    assert rec["provider"] == "openai-codex", rec
    assert "fallback_from" not in rec, rec
assert all("sensitive" not in rec for rec in records), "unexpected topic verdict"
PY
grep -rq "$canary" "$work/triage-gate/drafts" || fail "keyword draft not in common storage"
grep -q '되묻기\|calendar-calls' /dev/null 2>/dev/null || true
grep -q -- '--text' "$work/calendar-calls.log" || fail "calendar delegation argv missing --text"
[[ "$(send_calls)" == 0 ]] || fail "triage tick sent mail before any approval"
[[ ! -f "$TRIAGE_APPROVAL_LOG" ]] || fail "triage tick wrote approval records before confirm"

# --- 6) idempotency: same tick again -> zero new work ---------------------------
tri process --no-sync --no-post > "$work/t2.out" || fail "second process failed"
grep -q '^PROCESSED n=0' "$work/t2.out" || fail "reprocess was not idempotent"

keyword_draft="$(PYTHONPATH="$script_dir" python3 -c 'import triage_gate; print(next(d["id"] for d in triage_gate.list_drafts() if d["uid"] == "u-101"))')"
other_draft="$(PYTHONPATH="$script_dir" python3 -c 'import triage_gate; print(next(d["id"] for d in triage_gate.list_drafts() if d["uid"] == "u-104"))')"
[[ -n "$keyword_draft" && -n "$other_draft" ]] || fail "draft ids not found"

# --- 7) no approval -> 0 send ----------------------------------------------------
if tri confirm --draft "$other_draft" >/dev/null 2>&1; then
  fail "confirm succeeded without owner approval"
fi
[[ "$(send_calls)" == 0 ]] || fail "fail-closed confirm still sent mail"

# --- 8) signed injected confirm (when the W1-6 adapter is importable) -----------
python3 - "$script_dir" <<'PY' || fail "owner cancel reaction did not discard"
import argparse
import os
import sys

sys.path.insert(0, sys.argv[1])
import triage_cli
import triage_confirm
import triage_core
import triage_gate

owner = "owner-reaction-test"
channel = os.environ["SCENARIO_APPROVAL_CHANNEL_ID"]
draft = triage_gate.create_draft(
    uid="u-reaction-cancel", sender="owner <owner@example.invalid>",
    mail_subject="취소 반응", to="owner@example.invalid", subject="Re: 취소 반응",
    body="반응 취소 검증", category="important",
    flags=("reply_needed",),
)
draft = triage_gate.set_approval_binding(
    draft,
    kind="reply",
    surface="skill-approvals",
    channel_id=channel,
    policy_version=1,
)
draft = triage_gate.set_message_id(draft, "reaction-cancel-message", channel)
notices = []

def api(method, path, payload=None):
    del payload
    if method == "GET" and path == f"/channels/{channel}/messages/{draft['message_id']}":
        return {"content": f"draft sha256:{draft['sha256']}"}
    if method == "GET" and path.endswith(f"/reactions/{triage_confirm.quote(triage_confirm.CANCEL_EMOJI, safe='')}?limit=100"):
        return [{"id": owner, "bot": False}]
    if method == "GET" and path.endswith(f"/reactions/{triage_confirm.quote(triage_confirm.APPROVE_EMOJI, safe='')}?limit=100"):
        return [{"id": owner, "bot": False}]
    raise AssertionError(f"unexpected Discord call: {method} {path}")

def no_send(_draft, _approval):
    raise AssertionError("cancelled draft reached mailon send")

triage_confirm.owner_id = lambda: owner
triage_confirm._api = api
triage_confirm.dm_owner = lambda message: notices.append(message)
triage_gate.list_drafts = lambda: [draft]
triage_gate.execute_draft = no_send
triage_cli.cmd_process = lambda _args: 0
assert triage_cli.cmd_watch(argparse.Namespace()) == 0
assert len(notices) == 1 and "발송 취소" in notices[0], notices
assert "Re: 취소 반응" in notices[0] and draft["id"] in notices[0], notices
try:
    triage_gate.load_draft(draft["id"])
except triage_gate.GateError:
    pass
else:
    raise AssertionError("owner cancel reaction left a pending draft")
PY

# --- 9) signed injected confirm (when the W1-6 adapter is importable) -----------
confirm_leg="fail-closed-only"
if python3 -I -c '
import os, sys
sys.path.insert(0, os.path.expanduser(os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")))
import automation.interop.injection_adapter' 2>/dev/null; then
  export E2E_TEST_MODE=1
  INTEROP_E2E_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  export INTEROP_E2E_SECRET
  export INTEROP_CONFIG="$work/interop-config.json"
  test_owner="999900000000000625"
  printf '{"owner_id": "%s"}' "$test_owner" > "$INTEROP_CONFIG"

  persist_e2e_binding() {
    python3 - "$script_dir" "$1" <<'PY'
import os
import sys

sys.path.insert(0, sys.argv[1])
import triage_gate

draft = triage_gate.load_draft(sys.argv[2])
triage_gate.set_approval_binding(
    draft,
    kind="reply",
    surface="skill-approvals",
    channel_id=os.environ["SCENARIO_APPROVAL_CHANNEL_ID"],
    policy_version=1,
)
PY
  }

  persist_e2e_binding "$other_draft"

  tri sign --draft "$other_draft" --out "$work/forged.json" \
    --user-id "$test_owner" --forge-signature >/dev/null
  tri confirm --draft "$other_draft" --injection-file "$work/forged.json" >/dev/null 2>&1 \
    && fail "forged signature was accepted"
  tri sign --draft "$other_draft" --out "$work/wrong-owner.json" --user-id "111100000000000111" >/dev/null
  tri confirm --draft "$other_draft" --injection-file "$work/wrong-owner.json" >/dev/null 2>&1 \
    && fail "non-owner approval was accepted"
  [[ "$(send_calls)" == 0 ]] || fail "rejected confirm still sent mail"

  tri sign --draft "$other_draft" --out "$work/ok.json" --user-id "$test_owner" >/dev/null
  tri confirm --draft "$other_draft" --injection-file "$work/ok.json" \
    | grep -q "^SENT draft=$other_draft method=signed_injection_e2e" || fail "signed confirm did not send"
  [[ "$(send_calls)" == 1 ]] || fail "expected exactly 1 mailon send call after confirm"
  grep -q 'mailon.main send' "$work/mailon-send-calls.log" || fail "executed call is not mailon send"
  grep -q '"action":"external_effect.approval"' "$TRIAGE_APPROVAL_LOG" \
    || fail "external-effect approval record missing"
  grep -q '"target_id":"tool:mailon_send:python3"' "$TRIAGE_APPROVAL_LOG" \
    || fail "approval record target mismatch"
  grep -q '"action":"mail.reply_send"' "$TRIAGE_APPROVAL_LOG" || fail "mail.reply_send audit missing"
  grep -q '"status":"sent"' "$work/triage-gate/send-log.jsonl" || fail "send log missing"
  grep -q 'example.invalid' "$work/triage-gate/send-log.jsonl" && fail "send log leaked recipient"
  tri confirm --draft "$other_draft" --injection-file "$work/ok.json" >/dev/null 2>&1 \
    && fail "executed draft was confirmable twice"
  [[ "$(send_calls)" == 1 ]] || fail "double confirm sent a second mail"

  # --- 10) approval REJECT -> discard + 0 further send --------------------------
  tri discard --draft "$keyword_draft" | grep -q "^DISCARDED" || fail "discard failed"
  tri confirm --draft "$keyword_draft" --injection-file "$work/ok.json" >/dev/null 2>&1 \
    && fail "discarded draft was confirmable"
  [[ "$(send_calls)" == 1 ]] || fail "discarded draft still sent"

  # --- 11) 2 consecutive approved-send failures -> NO-GO + W4-1N record ---------
  printf 'fail' > "$work/stub_send_mode.txt"
  for n in 1 2; do
    fid="$(python3 - "$script_dir" "$n" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import triage_gate
record = triage_gate.create_draft(
    uid=f"u-fail-{sys.argv[2]}", sender="셀프 <self@example.invalid>",
    mail_subject="실패 주입", to="self@example.invalid", subject="Re: 실패 주입",
    body="실패 경로 검증", category="important",
    flags=("reply_needed",))
print(record["id"])
PY
)"
    persist_e2e_binding "$fid"
    tri sign --draft "$fid" --out "$work/fail-$n.json" --user-id "$test_owner" >/dev/null
    rc=0; tri confirm --draft "$fid" --injection-file "$work/fail-$n.json" >/dev/null 2>&1 || rc=$?
    [[ "$rc" == 6 ]] || fail "failed send #$n not exit 6 (rc=$rc)"
  done
  calls_after_fails="$(send_calls)"  # fail-mode stub still logs its invocation
  grep -q '"mode": "no-go"' "$TRIAGE_MAIL_MODE_FILE" || fail "mail-mode not downgraded to no-go"
  grep -q '"source": "W4-2-runtime"' "$TRIAGE_MAIL_MODE_FILE" || fail "downgrade source missing"
  grep -q '"mode": "full-go"' "$TRIAGE_MAIL_MODE_REPO" || fail "downgrade mutated the repo seed"
  grep -q '"event":"w4-1n-switch"' "$work/triage-gate/mode-switch.jsonl" \
    || fail "W4-1N switch record missing"
  # post-downgrade: even an approved confirm refuses (mode fail-closed)
  printf 'ok' > "$work/stub_send_mode.txt"
  fid2="$(python3 - "$script_dir" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import triage_gate
record = triage_gate.create_draft(
    uid="u-postmode", sender="셀프 <self@example.invalid>", mail_subject="모드 검증",
    to="self@example.invalid", subject="Re: 모드 검증", body="모드 fail-closed",
    category="important", flags=("reply_needed",))
print(record["id"])
PY
)"
  persist_e2e_binding "$fid2"
  tri sign --draft "$fid2" --out "$work/postmode.json" --user-id "$test_owner" >/dev/null
  rc=0; tri confirm --draft "$fid2" --injection-file "$work/postmode.json" >/dev/null 2>&1 || rc=$?
  [[ "$rc" == 3 ]] || fail "post-downgrade confirm not refused with exit 3 (rc=$rc)"
  [[ "$(send_calls)" == "$calls_after_fails" ]] || fail "post-downgrade confirm still sent"
  unset E2E_TEST_MODE INTEROP_E2E_SECRET INTEROP_CONFIG
  confirm_leg="signed-confirm"
else
  rm -f "$TRIAGE_MAIL_MODE_FILE"
  printf '{"decided_at": "x", "mode": "no-go", "source": "W4-2-runtime"}' > "$TRIAGE_MAIL_MODE_FILE"
fi

# --- 11b) an approved send stopped at compose: counted, told in its thread, waits --
printf 'compose' > "$work/stub_send_mode.txt"
calls_before_compose="$(send_calls)"
python3 - "$script_dir" > "$work/compose-fail.out" 2>&1 <<'PY' || { cat "$work/compose-fail.out"; fail "compose failure leg failed"; }
import argparse, json, sys
sys.path.insert(0, sys.argv[1])
import mail_preflight, triage_cli, triage_confirm, triage_gate, triage_mode

draft = triage_gate.create_draft(
    uid="u-compose-fail", sender="셀프 <self@example.invalid>", mail_subject="전달 실패",
    to="self@example.invalid", subject="FW: 전달 실패", body="compose 단계 실패 검증",
    category="important", flags=("reply_needed",))
draft = {**draft, "message_id": "m-compose-fail"}
notices = []
triage_mode.effective_mode = lambda: "full-go"
triage_confirm.owner_id = lambda: "owner-scenario"
triage_confirm.resolve_reaction = lambda _d: triage_confirm.APPROVE_EMOJI
triage_confirm.notify_result = lambda d, content, outcome="": notices.append(content)
triage_cli._remind_pending = lambda *_a: None
triage_cli.triage_approval.expire_retired_approval = lambda _d: False
mail_preflight.guarded_execute_draft = triage_gate.execute_draft
triage_gate.list_drafts = lambda: [draft]
assert triage_cli.cmd_watch(argparse.Namespace()) == 6, "a failed approved send must fail the tick"
stored = json.loads(triage_gate._draft_path(draft["id"]).read_text(encoding="utf-8"))
assert stored["status"] == "pending" and stored["failed_attempts"] == 1, stored
assert stored["last_error"]["step"] == "compose_open", stored["last_error"]
assert len(notices) == 1 and notices[0].startswith("⚠️ 발송 실패"), notices
assert "compose_not_ready" in notices[0] and "다음 자동 재시도" in notices[0], notices
triage_gate.list_drafts = lambda: [stored]
assert triage_cli.cmd_watch(argparse.Namespace()) == 0
assert len(notices) == 1, "a draft waiting for its retry time must not be tried or told again"
print("COMPOSE-FAIL-LEG-OK")
PY
grep -q '^COMPOSE-FAIL-LEG-OK' "$work/compose-fail.out" || fail "compose failure leg did not finish"
grep -q '^RETRY-WAIT draft=' "$work/compose-fail.out" || fail "second tick did not wait for the retry time"
[[ "$(send_calls)" == "$((calls_before_compose + 1))" ]] || fail "compose failure leg sent more than once"
printf 'ok' > "$work/stub_send_mode.txt"

# --- 12) watch refusals: E2E env + non-full-go mode ------------------------------
calls_before_watch="$(send_calls)"
rc=0; E2E_TEST_MODE=1 tri watch >/dev/null 2>&1 || rc=$?
[[ "$rc" == 3 ]] || fail "watch under E2E_TEST_MODE not refused (rc=$rc)"
tri watch > "$work/watch-mode.out" || fail "mode-skip watch tick failed"
grep -q '^MODE-SKIP mode=no-go' "$work/watch-mode.out" || fail "watch did not skip on no-go"
[[ "$(send_calls)" == "$calls_before_watch" ]] || fail "mode-skip watch sent mail"

echo "SCENARIO-PASS mail wrapper+triage offline contract leg=$confirm_leg (common-model-route/no-send-before-approval/idempotent/no-go-downgrade+resolve/compose-fail-notice+retry-wait)"

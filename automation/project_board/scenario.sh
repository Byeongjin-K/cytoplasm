#!/usr/bin/env bash
# Sandbox end-to-end run of the project board through its real CLI, with no Discord and no
# real mail: a synthetic MailOn runtime, a local OpenAI-compatible model stub (the config
# swap path), the real watcher tick over a fake card transport (owner ✅), then progress
# and the dashboard file. Prints SCENARIO-PASS or exits non-zero at the first failed check.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d)"
trap 'kill "${stub_pid:-0}" 2>/dev/null || true; rm -rf "$work"' EXIT
export HOME="$work/home" PYTHONPATH="$repo_root" PROJECT_BOARD_ROOT="$work/board"
export MAIL_WRAPPER_REPO="$work/mailon" PROJECT_BOARD_CONFIG="$work/config.json"
mkdir -p "$HOME" "$MAIL_WRAPPER_REPO/data" "$MAIL_WRAPPER_REPO/mails" "$MAIL_WRAPPER_REPO/att"

python3 - "$MAIL_WRAPPER_REPO" <<'PY'
import sqlite3, sys
from pathlib import Path
root = Path(sys.argv[1])
(root / "att" / "양식.txt").write_text("synthetic attachment", encoding="utf-8")
(root / "mails" / "s-1.md").write_text(
    "---\nuid: s-1\nfolder: inbox\nsubject: 프로젝트A 중간보고 자료 요청\nfrom: 담당자1 <one@example.invalid>\n"
    "date: 2026-10-13T09:00:00\nattachments:\n  - 양식.txt\n---\n\n# 프로젝트A 중간보고 자료 요청\n\n"
    "중간보고서 초안을 10월 20일까지 제출해 주세요. 실험 결과표도 함께 정리 부탁드립니다.\n", encoding="utf-8")
db = sqlite3.connect(root / "data" / "state.db")
db.executescript(
    "CREATE TABLE messages (uid TEXT PRIMARY KEY, folder TEXT, subject TEXT, sender TEXT, recv_date TEXT,"
    " markdown_path TEXT, saved_at INTEGER);"
    "CREATE TABLE attachments (uid TEXT, filename TEXT, href TEXT, status TEXT, size_bytes INTEGER,"
    " error_msg TEXT, attempts INTEGER, local_path TEXT, first_seen INTEGER, last_attempt INTEGER);")
db.execute("INSERT INTO messages VALUES ('s-1','inbox','프로젝트A 중간보고 자료 요청','one','2026-10-13','mails/s-1.md',100)")
db.execute("INSERT INTO attachments VALUES ('s-1','양식.txt','x','ok',20,NULL,1,'att/양식.txt',1,1)")
db.commit()
PY

python3 - "$work/port" <<'PY' &
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
answer = {"relevant": True, "project": "프로젝트A", "project_owner": "담당자1", "summary": "중간보고 자료 요청",
          "todos": [{"title": "중간보고서 초안 제출", "owner": "담당자1", "due": "2026-10-20", "due_reason": "원문 기한"},
                    {"title": "실험 결과표 정리", "owner": "", "due": None, "due_reason": "원문에 기한 없음"}]}
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps({"model": body["model"], "choices": [{"message": {"content": json.dumps(answer)}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass
server = ThreadingHTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(server.server_address[1]))
server.serve_forever()
PY
stub_pid=$!
for _ in $(seq 50); do [ -s "$work/port" ] && break; sleep 0.1; done
printf '{"llm":{"provider":"openai-compatible","base_url":"http://127.0.0.1:%s/v1","model":"sandbox-local"}}' \
  "$(cat "$work/port")" > "$PROJECT_BOARD_CONFIG"

cli() { python3 -m automation.project_board "$@"; }
check() { if ! grep -q -- "$1" <<<"$2"; then echo "SCENARIO-FAIL: expected [$1] in: $2" >&2; exit 1; fi; }

out="$(cli status)"; check "openai-compatible 127.0.0.1" "$out"
out="$(cli analyze --mail s-1 --no-post)"; check "PROPOSAL proposed" "$out"; check "todos=2" "$out"
first="$(sed -n 's/.*proposal=\(pb-[0-9a-f]*\).*/\1/p' <<<"$out")"
out="$(cli analyze --mail s-1 --no-post)"; check "already-proposed $first" "$out"
out="$(cli revise "$first" --set-due 1=2026-10-24 --drop 2 --no-post)"; check "revision=2" "$out"; check "todos=1" "$out"
second="$(sed -n 's/.*proposal=\(pb-[0-9a-f]*\).*/\1/p' <<<"$out")"

python3 - "$first" "$second" <<'PY'
import sys
from datetime import UTC, datetime
from automation.interop.approval_lifecycle import Outcome
from automation.interop.approval_surface import POLICY_VERSION, ApprovalBinding, ApprovalKind, required_surface
from automation.project_board import proposal_gate
from automation.project_board.proposal_watch import run_tick
from automation.project_board.service import Board

class Fake:
    owner_id = "100"
    def __init__(self): self.messages, self.reactions = {}, {}
    def api(self, *a, **k): return {}
    def post_message(self, channel, content):
        mid = str(900 + len(self.messages)); self.messages[mid] = content; return mid
    def add_reaction(self, channel, mid, emoji): self.reactions.setdefault((mid, emoji), []).append(("7", True))
    def get_message(self, channel, mid): return self.messages.get(mid)
    def get_reaction_users(self, channel, mid, emoji): return tuple(self.reactions.get((mid, emoji), ()))
    def delete_message(self, channel, mid): self.messages.pop(mid, None)

board, fake = Board.default(), Fake()
binding = ApprovalBinding(ApprovalKind.PROJECT_BOARD, required_surface(ApprovalKind.PROJECT_BOARD), "4242", POLICY_VERSION, "77")
proposal_gate.reuse_request_thread = lambda *a: None
proposal_gate.resolve_new_binding = lambda *a, **k: binding
runtime = proposal_gate.DiscordRuntime(fake, object(), "100")
for request_id in sys.argv[1:]:
    record = board.proposals.get(request_id)
    assert proposal_gate.request_approval(record, board.proposals, runtime).outcome is Outcome.POSTED
card = board.proposals.get(sys.argv[2])
assert board.proposals.get(sys.argv[1]).status == "superseded", "revision must supersede the first card"
assert card.action_hash in fake.messages[card.message_id] and "2026-10-24" in fake.messages[card.message_id]
fake.reactions[(card.message_id, "\u2705")].append(("100", False))
run_tick(board.proposals, board.store, runtime, token="", now=datetime.now(UTC), on_change=lambda: None)
assert board.proposals.get(sys.argv[2]).status == "applied"
print("CARD-APPROVED", card.request_id)
PY

out="$(cli build)"; dash="${out#DASHBOARD }"
check "중간보고서 초안 제출" "$(cat "$dash")"; check "2026-10-24" "$(cat "$dash")"
if grep -q "실험 결과표 정리" "$dash"; then echo "SCENARIO-FAIL: dropped todo reached the dashboard" >&2; exit 1; fi
out="$(cli progress "$second-1" --status 완료 --note 제출함)"; check "status=완료" "$out"
check "완료" "$(cat "$dash")"
out="$(cli digest)"; check "DIGEST" "$out"
echo "SCENARIO-PASS proposal=$second dashboard=$dash"

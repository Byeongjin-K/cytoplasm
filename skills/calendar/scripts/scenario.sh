#!/usr/bin/env bash
# Sandbox scenario for the calendar skill (W1-8 pipeline stage 1 / post-mount smoke).
# Runs fully offline in a temp dir against a stub gws binary: proves
# no-mutation-before-confirm, fail-closed confirm, ambiguous-time re-ask,
# forged-signature rejection, and (when the W1-6 injection adapter is
# importable) the signed-confirm execute path with approval records plus the
# delete round-trip and refusal-zero-change.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY || "$secret" == DUMMY-* ]] || fail "secret is not a DUMMY sandbox value (real secrets forbidden in sandbox)"
if [[ "$secret" == *sk-* || "$secret" == *ghp_* || "$secret" == *"Bot "* ]]; then
  fail "secret matches a real-token shape"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
export CALENDAR_GATE_DIR="$work/gate" CALENDAR_APPROVAL_LOG="$work/approvals.jsonl"
export CALENDAR_GWS_BIN="$work/gws-stub"
# Peer registry fixture: calendar_cli always resolves named peers, and the default
# registry lives at /srv/autophagy-agents, which the sandbox has no access to. Point
# it at a local fixture so the scenario tests routing, not the node's filesystem.
# Bot ids here are deliberately fake snowflakes -- the registry only needs to parse
# and to name the peers the scenario drives.
export CALENDAR_PEERS_CONFIG="$work/peers.yaml"
cat > "$CALENDAR_PEERS_CONFIG" <<'YAML'
version: 1
peers:
  agent-cha:
    bot_user_id: "100000000000000001"
    bot_name: BAEKDONGCHA-Agent
  peer-test:
    bot_user_id: "100000000000000002"
    bot_name: BAEKDONGCHA-Peer
YAML
cli() { python3 "$script_dir/calendar_cli.py" "$@"; }

cat > "$work/gws-stub" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >> "$(dirname "$0")/gws-calls.log"
case "$*" in
  *"events insert"*|*"events patch"*) printf '{"id":"stub-event-1","status":"confirmed"}\n' ;;
  *"events get"*) printf '{"id":"stub-event-1","summary":"실험 미팅","start":{"dateTime":"2031-10-01T15:00:00+09:00"},"end":{"dateTime":"2031-10-01T16:00:00+09:00"}}\n' ;;
  *"events list"*) printf '{"items":[]}\n' ;;
esac
SH
chmod +x "$work/gws-stub"
calls() { if [ -f "$work/gws-calls.log" ]; then wc -l < "$work/gws-calls.log"; else echo 0; fi; }
# Creates read the same day first (same-event check); only writes count as mutations.
mutations() { if [ -f "$work/gws-calls.log" ]; then grep -cE 'events (insert|patch|update|delete)' "$work/gws-calls.log" || true; else echo 0; fi; }

# --- 1) ambiguous time is re-asked, nothing recorded -------------------------
set +e
amb_out="$(cli draft-create --text "다음주쯤 미팅" 2>&1)"; amb_rc=$?
set -e
[[ "$amb_rc" -eq 5 ]] || fail "ambiguous time not exit 5 (rc=$amb_rc)"
grep -q "AMBIGUOUS-TIME 되묻기" <<<"$amb_out" || fail "ambiguous output lacks re-ask marker"
set +e
bare_out="$(cli draft-create --text "내일 3시 미팅" 2>&1)"; bare_rc=$?
set -e
[[ "$bare_rc" -eq 5 ]] || fail "bare 1-12시 not re-asked (rc=$bare_rc)"
[[ "$(calls)" -eq 0 ]] || fail "ambiguous stage invoked gws"

# --- 2) create draft mutates NOTHING -----------------------------------------
out="$(cli draft-create --text "내일 오후 3시 실험 미팅")"
grep -q "CHANGE-SUMMARY" <<<"$out" || fail "change summary missing"
grep -q "제목: 실험 미팅" <<<"$out" || fail "summary title wrong"
draft_id="$(sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p' <<<"$out")"
[[ -n "$draft_id" ]] || fail "draft id missing from output"
[[ "$(mutations)" -eq 0 ]] || fail "draft stage invoked gws (mutation before confirm)"
[[ ! -f "$CALENDAR_APPROVAL_LOG" ]] || fail "draft stage wrote an approval record"

# --- 3) confirm without any confirmation fails closed ------------------------
if cli confirm --draft "$draft_id" >/dev/null 2>&1; then
  fail "confirm succeeded without owner confirmation"
fi
[[ "$(mutations)" -eq 0 ]] || fail "fail-closed confirm still invoked gws"
[[ ! -f "$CALENDAR_APPROVAL_LOG" ]] || fail "fail-closed confirm wrote an approval record"

# --- 4) signed injected confirm (only when W1-6 adapter is importable) -------
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

  cli sign --draft "$draft_id" --out "$work/forged.json" \
    --user-id "$test_owner" --forge-signature >/dev/null
  if cli confirm --draft "$draft_id" --injection-file "$work/forged.json" >/dev/null 2>&1; then
    fail "forged signature was accepted"
  fi
  cli sign --draft "$draft_id" --out "$work/wrong-owner.json" --user-id "111100000000000111" >/dev/null
  if cli confirm --draft "$draft_id" --injection-file "$work/wrong-owner.json" >/dev/null 2>&1; then
    fail "non-owner confirmation was accepted"
  fi
  [[ "$(mutations)" -eq 0 ]] || fail "rejected confirm still invoked gws"
  [[ ! -f "$CALENDAR_APPROVAL_LOG" ]] || fail "rejected confirm wrote an approval record"

  cli sign --draft "$draft_id" --out "$work/ok.json" --user-id "$test_owner" >/dev/null
  cli confirm --draft "$draft_id" --injection-file "$work/ok.json" \
    | grep -q '^EXECUTED action=create event=stub-event-1' || fail "signed confirm did not execute"
[[ "$(mutations)" -eq 1 ]] || fail "expected exactly one write after confirm"
grep -q "events insert" "$work/gws-calls.log" || fail "executed call is not events insert"
grep -q "events get" "$work/gws-calls.log" || fail "post-write events get missing"
  grep -q '"action":"external_effect.approval"' "$CALENDAR_APPROVAL_LOG" \
    || fail "external-effect approval record missing"
  grep -q '"action":"calendar.create"' "$CALENDAR_APPROVAL_LOG" \
    || fail "calendar.create audit record missing"
  grep -q '"method":"signed_injection_e2e"' "$CALENDAR_APPROVAL_LOG" \
    || fail "approval method missing"

  # --- delete round-trip ------------------------------------------------------
  del_out="$(cli draft-delete --event-id stub-event-1 --label "실험 미팅")"
  del_id="$(sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p' <<<"$del_out")"
  [[ -n "$del_id" ]] || fail "delete draft id missing"
  cli sign --draft "$del_id" --out "$work/del.json" --user-id "$test_owner" >/dev/null
  cli confirm --draft "$del_id" --injection-file "$work/del.json" \
    | grep -q '^EXECUTED action=delete' || fail "delete confirm did not execute"
  grep -q "events delete" "$work/gws-calls.log" || fail "delete call missing from gws log"
  grep -q '"action":"calendar.delete"' "$CALENDAR_APPROVAL_LOG" || fail "calendar.delete audit missing"

  # --- refusal → zero change --------------------------------------------------
  before_calls="$(mutations)"
  ref_out="$(cli draft-create --text "모레 14:30 거부 테스트")"
  ref_id="$(sed -n 's/^DRAFT-CREATED id=\([0-9a-f]*\) .*/\1/p' <<<"$ref_out")"
  cli discard --draft "$ref_id" | grep -q "^DISCARDED" || fail "discard failed"
  if cli confirm --draft "$ref_id" >/dev/null 2>&1; then
    fail "confirm succeeded on a discarded draft"
  fi
  [[ "$(mutations)" -eq "$before_calls" ]] || fail "refused draft changed the calendar"

  unset E2E_TEST_MODE INTEROP_E2E_SECRET INTEROP_CONFIG
  confirm_leg="signed-confirm"
fi

# --- 5) read path is gate-free -----------------------------------------------
cli list --days 1 | grep -q '^LISTED n=0' || fail "read path list failed"

printf 'SCENARIO-PASS leg=%s secret_len=%s account=%s\n' "$confirm_leg" "${#secret}" "$(whoami)"

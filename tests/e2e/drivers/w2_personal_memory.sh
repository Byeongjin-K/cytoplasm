#!/usr/bin/env bash
# W2-6 scenario driver: pushes the remote actuator + fixtures to the agent
# account on <primary-node>, runs the bank UNATTENDED under E2E_TEST_MODE=1, then
# judges the emitted observations against the scenario YAML expect blocks.
#
# Usage: w2_personal_memory.sh <scenario.yaml> <report_dir>
# Exit:  0 = every case matched its expected observables, 1 = mismatch/error.
set -euo pipefail

SCENARIO="$(readlink -f "$1")"
REPORT_DIR="$(mkdir -p "$2" && readlink -f "$2")"
ROOT="$(cd "$(dirname "$SCENARIO")/../../.." && pwd)"
DRIVERS="$ROOT/tests/e2e/drivers"
FIXTURES="$ROOT/tests/e2e/fixtures/w2-personal-memory"
HOST="$(sed -n 's/^remote_host:[[:space:]]*//p' "$SCENARIO" | head -1)"
# 공개 트리의 시나리오는 설치별 호스트 대신 자리표시자(<primary-node>)를 싣는다 — 노드 설정에서 푼다.
if [[ -z "$HOST" || "$HOST" == *"<"* ]]; then
  eval "$(python3 "$ROOT/automation/node_config_sh.py" --print-env)"
  HOST="$NODE_DEPLOY_SSH_HOST"
fi
ACCOUNT="$(sed -n 's/^remote_account:[[:space:]]*//p' "$SCENARIO" | head -1)"
PUSH_DIR="/home/$ACCOUNT/.cache/w2e6-bank-push"
RUN_TIMEOUT=900

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp "$DRIVERS/w2_personal_memory_remote.py" "$stage/remote.py"
mkdir -p "$stage/fixtures"
cp "$FIXTURES"/* "$stage/fixtures/"

# Push (tar over ssh/sudo — avoids nested-quoting and stdin-eating pitfalls).
tar -C "$stage" -cf - . | ssh "$HOST" "sudo -n -u $ACCOUNT -H bash -c \
  'umask 077; rm -rf $PUSH_DIR; mkdir -p $PUSH_DIR; tar -xf - -C $PUSH_DIR'"

# Run unattended (stdin closed; E2E_TEST_MODE only on this process tree).
run_log="$REPORT_DIR/remote-run.log"
set +e
timeout "$RUN_TIMEOUT" ssh "$HOST" "sudo -n -u $ACCOUNT -H bash -lc \
  'set -euo pipefail; mkdir -p $PUSH_DIR/home; export HOME=$PUSH_DIR/home; cd ~; E2E_TEST_MODE=1 python3 $PUSH_DIR/remote.py \
   --fixtures $PUSH_DIR/fixtures'" </dev/null >"$run_log" 2>&1
run_rc=$?
set -e

# Remote scratch cleanup regardless of outcome.
ssh "$HOST" "sudo -n -u $ACCOUNT -H rm -rf $PUSH_DIR" </dev/null || true

if [[ $run_rc -ne 0 ]]; then
  echo "FAIL w2-personal-memory: remote actuator rc=$run_rc (see $run_log)"
  exit 1
fi

sed -n 's/^OBS-JSON: //p' "$run_log" | head -1 >"$REPORT_DIR/observations.json"
if [[ ! -s "$REPORT_DIR/observations.json" ]]; then
  echo "FAIL w2-personal-memory: no OBS-JSON line in remote output (see $run_log)"
  exit 1
fi

python3 "$DRIVERS/judge_expectations.py" "$SCENARIO" \
  "$REPORT_DIR/observations.json" "$REPORT_DIR/verdict.txt"

#!/usr/bin/env bash
# E2E scenario-bank runner (W2-6 foundation; W3~W6 add scenarios to the same
# bank; Final F3 calls `tests/e2e/run_bank.sh --all`).
#
# Every scenario is a YAML under tests/e2e/scenarios/ declaring a `driver:`
# script (relative to repo root). The driver receives <scenario.yaml>
# <report_dir>, runs UNATTENDED, and exits 0 only when every case matched its
# expected observables. This runner aggregates: exit 0 iff all scenarios pass.
#
# Usage:
#   run_bank.sh [--all]                 run every scenario (default)
#   run_bank.sh --scenario <id> [...]   run only matching scenario file ids
#   run_bank.sh --list                  list discovered scenarios
#   run_bank.sh --report-dir <dir>      where reports go
#                                       (default /tmp/e2e-bank-<UTC stamp>)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCENARIOS_DIR="$ROOT/tests/e2e/scenarios"
REPORT_DIR=""
LIST_ONLY=0
declare -a ONLY=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) shift ;;
    --scenario) ONLY+=("$2"); shift 2 ;;
    --report-dir) REPORT_DIR="$2"; shift 2 ;;
    --list) LIST_ONLY=1; shift ;;
    *) echo "usage: run_bank.sh [--all|--scenario <id>|--list|--report-dir <dir>]" >&2
       exit 2 ;;
  esac
done

mapfile -t scenarios < <(find "$SCENARIOS_DIR" -maxdepth 1 -name '*.yaml' | sort)
if [[ ${#ONLY[@]} -gt 0 ]]; then
  declare -a filtered=()
  for yaml in "${scenarios[@]}"; do
    name="$(basename "$yaml" .yaml)"
    for want in "${ONLY[@]}"; do
      [[ "$name" == "$want" ]] && filtered+=("$yaml")
    done
  done
  scenarios=("${filtered[@]}")
fi
if [[ ${#scenarios[@]} -eq 0 ]]; then
  echo "run_bank: no scenarios found under $SCENARIOS_DIR" >&2
  exit 1
fi

if [[ $LIST_ONLY -eq 1 ]]; then
  for yaml in "${scenarios[@]}"; do basename "$yaml" .yaml; done
  exit 0
fi

REPORT_DIR="${REPORT_DIR:-/tmp/e2e-bank-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$REPORT_DIR"

failed=0
declare -a summary=()
for yaml in "${scenarios[@]}"; do
  name="$(basename "$yaml" .yaml)"
  driver="$(sed -n 's/^driver:[[:space:]]*//p' "$yaml" | head -1)"
  live_retry="$(sed -n 's/^live_retry:[[:space:]]*//p' "$yaml" | head -1)"
  # A scenario whose subject was retired by a release switch is reported, not run (2026-10-19:
  # report-hub, peer coordination and Hermes memory). Removing the switch file brings it back.
  retired_when="$(sed -n 's/^retired_when:[[:space:]]*//p' "$yaml" | head -1)"
  if [[ "$retired_when" == ?*=?* ]]; then
    flag_file="${BANK_RELEASE_FLAGS_DIR:-$HOME/.hermes/release-flags}/${retired_when%%=*}"
    if [[ -f "$flag_file" && ! -L "$flag_file" && "$(cat -- "$flag_file"; printf x)" == "${retired_when#*=}x" ]]; then
      echo "=== scenario $name RETIRED (release switch ${retired_when%%=*}=${retired_when#*=})"
      summary+=("RETIRED $name (${retired_when%%=*})")
      continue
    fi
  fi
  if [[ -z "$driver" || ! -f "$ROOT/$driver" ]]; then
    summary+=("FAIL $name (driver missing: ${driver:-<none>})")
    failed=1
    continue
  fi

  # A marked live integration may transiently lose an SSH/Discord request or
  # observe a gateway while it is reconnecting.  Retry the *entire* scenario
  # once so its original exact-match assertions still decide the result.
  # Unmarked (including every offline) scenario is deliberately one attempt:
  # a failure there is deterministic evidence, not variability to mask.
  attempts=1
  [[ "$live_retry" == "true" ]] && attempts=2
  passed=0
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    scenario_report_dir="$REPORT_DIR/$name"
    [[ "$attempts" -gt 1 ]] && scenario_report_dir+="/attempt-$attempt"
    echo "=== scenario $name (driver $driver; attempt $attempt/$attempts; live_retry=${live_retry:-false}) ==="
    if bash "$ROOT/$driver" "$yaml" "$scenario_report_dir"; then
      passed=1
      break
    fi
    if [[ "$attempt" -lt "$attempts" ]]; then
      echo "RETRY $name: live integration attempt $attempt/$attempts failed; retrying once after 15s"
      sleep 15
    fi
  done
  if [[ "$passed" -eq 1 ]]; then
    summary+=("PASS $name")
  else
    summary+=("FAIL $name")
    failed=1
  fi
done

echo
echo "=== BANK SUMMARY (reports: $REPORT_DIR) ==="
printf '%s\n' "${summary[@]}"
if [[ $failed -eq 0 ]]; then
  echo "BANK RESULT: ALL SCENARIOS PASSED"
else
  echo "BANK RESULT: FAILURES PRESENT"
fi
exit "$failed"

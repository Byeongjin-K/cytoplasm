#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi

source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/cost-report/send_cost_report.py" \
  "$repo_root/automation/cost-report/deploy.sh" "$repo_root/automation/cost-report/deploy-manifest.txt" || exit 4

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "$script")"
}

# 비용 보고는 cron sandbox가 찾는 계정 홈에만 복사해야 다른 계정의 비밀 경계를 넘지 않는다.
run_agent() { run_account "$NODE_AGENT_ACCOUNT" "$1"; }
source "$repo_root/automation/deploy_push.sh"
push_file "$repo_root/automation/cost-report/send_cost_report.py" '.hermes/scripts/send_cost_report.py'
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer daily-cost-report "0 9 * * *" send_cost_report.py local COST_REPORT_SOFT_CAP

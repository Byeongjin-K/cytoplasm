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
deploy_provenance_check "$repo_root" "$repo_root/automation/selfskill_audit" || exit 4

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "$script")"
}

source "$repo_root/automation/deploy_push.sh"
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
for account in "$NODE_AGENT_ACCOUNT" "$NODE_PEER_ACCOUNT"; do
  run_agent() { run_account "$account" "$1"; }
  push_file "$repo_root/automation/selfskill_audit/cron/selfskill_audit_watch.py" '.hermes/scripts/selfskill_audit_watch.py'
  run_account "$account" 'grep -qx "timezone: Asia/Seoul" "$HOME/.hermes/config.yaml"'
done
# agent 는 사용자 타이머(P2), peer 는 Hermes cron 그대로 — peer 는 계정째 퇴역할 예정이라 옮기지 않는다(N3).
run_agent() { run_account "$NODE_AGENT_ACCOUNT" "$1"; }
converge_timer selfskill-audit-watch "0 9 * * *" selfskill_audit_watch.py local
run_agent() { run_account "$NODE_PEER_ACCOUNT" "$1"; }
converge_cron selfskill-audit-watch "0 9 * * *" selfskill_audit_watch.py local

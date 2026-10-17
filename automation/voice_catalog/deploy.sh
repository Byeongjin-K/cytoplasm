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
deploy_provenance_check "$repo_root" "$repo_root/automation/voice_catalog" || exit 4

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "$script")"
}

account="$NODE_AGENT_ACCOUNT"
run_agent() { run_account "$account" "$1"; }
source "$repo_root/automation/deploy_push.sh"
push_file "$repo_root/automation/voice_catalog/cron/voice_catalog_enroll_watch.py" '.hermes/scripts/voice_catalog_enroll_watch.py'
run_account "$account" 'grep -qx "timezone: Asia/Seoul" "$HOME/.hermes/config.yaml"'
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer voice-catalog-enroll "*/10 * * * *" voice_catalog_enroll_watch.py local
echo "DEPLOY-OK voice-catalog-enroll ($account@$host)"

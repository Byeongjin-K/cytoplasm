#!/usr/bin/env bash
# Ship the declared provider adapter. deploy_all owns agent+peer gateway activation.
set -euo pipefail
[[ $# -eq 0 ]] || { echo "usage: $0" >&2; exit 2; }
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
[[ -n "$host" ]] || { echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset" >&2; exit 3; }
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/memory_provider" || exit 4
source "$repo_root/automation/deploy_push.sh"
run_account() {
  ssh "$host" "sudo -n -u $(printf '%q' "$1") -H bash -lc $(printf '%q' "cd \"\$HOME\"; $2")"
}
for account in "$NODE_AGENT_ACCOUNT" "$NODE_PEER_ACCOUNT"; do
  run_agent() { run_account "$account" "$1"; }
  push_file "$repo_root/automation/memory_provider/plugin/__init__.py" \
    '.hermes/plugins/autophagy_recall/__init__.py'
done

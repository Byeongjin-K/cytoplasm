#!/usr/bin/env bash
# automation/effect_request/deploy.sh — deploy the generic effect-request reaction watcher.
#
# Like obsidian_write: the watcher imports automation.effect_request.* /
# automation.interop.* from the runtime root (release tree, fallback ops checkout).
# Deploy = (1) provenance-check the package against origin/main, (2) push the no-agent
# wrapper to ~/.hermes/scripts/, (3) idempotently register the reaction cron.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

# Deploy guard: refuse to deploy code that origin/main does not have.
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/effect_request" || exit 4

# No-agent reaction watcher wrapper.
push_file "$repo_root/automation/effect_request/cron/effect_request_watch.py" '.hermes/scripts/effect_request_watch.py'

# Idempotent cron registration (reaction resolve every 2m). --all so a paused job is seen.
source "$repo_root/automation/deploy_cron.sh"
converge_cron effect-request-watch "every 2m" effect_request_watch.py local
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all | grep -A3 effect-request-watch || true'

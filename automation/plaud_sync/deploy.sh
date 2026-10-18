#!/usr/bin/env bash
# automation/plaud_sync/deploy.sh — deploy the plaud lifelog sync watcher.
#
# Like memory_relocate: the watcher imports automation.plaud_sync.* /
# automation.obsidian_write.* / automation.interop.* from the runtime root, so it
# runs from the release tree (fallback: ops checkout). Deploy = (1) provenance-check
# the local package against origin/main, (2) ff-pull the ops checkout, (3) push the
# no-agent wrapper to ~/.hermes/scripts/, (4) idempotently register the cron.
#
# Order is enforced by the provenance guard: commit -> push -> deploy.
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
deploy_provenance_check "$repo_root" "$repo_root/automation/plaud_sync" || exit 4

# The watcher runs from the runtime root — align the ops checkout to origin/main.
ssh "$host" 'sudo -n -u ops -H git -C /srv/autophagy-agents pull --ff-only'

# No-agent watcher wrapper.
push_file "$repo_root/automation/plaud_sync/cron/plaud_sync_watch.py" '.hermes/scripts/plaud_sync_watch.py'
push_file "$repo_root/automation/plaud_sync/cron/auto_write_summary_watch.py" '.hermes/scripts/auto_write_summary_watch.py'

# Idempotent cron registration (reaction resolve every 10m; Plaud poll is gated
# inside the tick by PLAUD_SYNC_POLL_SECONDS, default 30m). --all so a paused job is seen.
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer plaud-sync-watch "every 10m" plaud_sync_watch.py local
converge_timer auto-write-daily-summary "30 21 * * *" auto_write_summary_watch.py local
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all | grep -A3 plaud-sync-watch || true'

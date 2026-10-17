#!/usr/bin/env bash
# automation/memory_curator/deploy.sh — deploy the memory-curator cron watcher.
#
# Only the wrapper is shipped: it imports `automation.memory_curator` from the release
# tree it resolves at start, so the package follows every release by itself.  The old
# account-home package copy is declared retired in deploy-manifest.txt and is left
# in place (nothing deletes node files).
#
# Order is enforced by the provenance guard: commit -> push -> deploy.  Only code that
# origin/main already has may reach prod (see automation/deploy_provenance.sh).
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

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $(printf '%q' "$account") -H bash -lc $(printf '%q' "$script")"
}

# push_file 은 원격 read-back 으로 착지를 확인한다 — 확인 없는 push 가 rc=0 으로
# 끝나면서 파일은 그대로였던 실측(2026-08-20)이 이 공유 구현의 이유다.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

# Deploy guard: refuse to push code that origin/main does not have.
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/memory_curator" || exit 4

# Cron watcher wrapper.
push_file "$repo_root/automation/memory_curator/cron/memory_curator_watch.py" '.hermes/scripts/memory_curator_watch.py'

# Idempotent cron registration (no-agent, LLM-free, every 30m). Use --all so a PAUSED
# job is still seen — plain `cron list` hides paused jobs and would duplicate the job.
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer memory-curator-watch "every 30m" memory_curator_watch.py local
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all | grep -A3 memory-curator-watch || true'

# Hermes memory caps (owner decision 2026-10-14): the curator's CAPS and both accounts'
# config.yaml read the same automation.hermes_memory_config.LIMITS. Gateways load the
# config at start; the composed profile also selects autophagy_recall without a second
# config.yaml writer. The agent profile also ignores agent-chat for YooDori;
# the peer retains the memory-only profile. activation=gateway restarts agent+peer.
source "$repo_root/automation/deploy_config.sh"
converge_config agent .hermes/config.yaml hermes-agent-profile-v1
converge_config peer .hermes/config.yaml hermes-memory-profile-with-recall-v1

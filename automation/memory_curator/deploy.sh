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
deploy_provenance_check "$repo_root" "$repo_root/automation/memory_curator" \
  "$repo_root/automation/release_approval_remote.sh" || exit 4

# The switch belongs to this release host, never the node. Check approval before
# any node writes: an existing but unapproved switch must not silently turn off.
dori_bot_id="$(PYTHONPATH="$repo_root" python3 -B -m automation.hermes_agent_chat_ignore --release-switch)" || exit 4
chat_channel="$(PYTHONPATH="$repo_root" python3 -B -m automation.yoodori_channel --release-switch)" || exit 4
chat_args=()
if [[ -n "$chat_channel" ]]; then
  head="$(git -C "$repo_root" rev-parse HEAD)" || exit 4
  deployed="$(ssh "$host" "readlink $(printf '%q' "${NODE_RELEASE_CURRENT:-/srv/autophagy-agent-current}")" </dev/null)" || deployed=""
  if [[ "${deployed##*/}" != "$head" ]]; then
    echo "YOODORI-CHANNEL-UNAUTHORIZED: deployed HEAD differs; unchanged." >&2
    exit 4
  fi
  if ! "$repo_root/automation/release_approval_remote.sh" authorize-yoodori-channel \
    --channel-id "$chat_channel" --head "$head"; then
    echo "YOODORI-CHANNEL-UNAUTHORIZED: this deployed HEAD did not approve the selected channel; unchanged." >&2
    exit 4
  fi
  chat_args=(--yoodori-channel "$chat_channel")
fi
dori_mode=off
if [[ -n "$dori_bot_id" ]]; then
  head="$(git -C "$repo_root" rev-parse HEAD)" || exit 4
  deployed="$(ssh "$host" "readlink $(printf '%q' "${NODE_RELEASE_CURRENT:-/srv/autophagy-agent-current}")" </dev/null)" || deployed=""
  if [[ "${deployed##*/}" != "$head" ]]; then
    echo "DORI-SWITCH-UNAUTHORIZED: deployed HEAD does not match this checkout; agent config unchanged." >&2
    exit 4
  fi
  if ! "$repo_root/automation/release_approval_remote.sh" authorize-dori-agent-chat \
    --bot-id "$dori_bot_id" --head "$head"; then
    echo "DORI-SWITCH-UNAUTHORIZED: this release HEAD did not approve the configured Dori bot; agent config unchanged." >&2
    exit 4
  fi
  dori_mode=on
fi

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
# config.yaml writer. The agent profile ignores agent-chat only with the host opt-in
# covered by this release approval; absence removes that ignore on the next deploy.
# the peer retains the memory-only profile. activation=gateway restarts agent+peer.
source "$repo_root/automation/deploy_config.sh"
converge_config agent .hermes/config.yaml hermes-agent-profile-v1 --dori-agent-chat "$dori_mode" "${chat_args[@]}"
converge_config peer .hermes/config.yaml hermes-memory-profile-with-recall-v1
converge_config agent .hermes/interop/config.json yoodori-channel-v1 --yoodori-channel "${chat_channel:-off}"
converge_config peer .hermes/interop/config.json yoodori-channel-v1 --yoodori-channel "${chat_channel:-off}"
converge_config ops private/channel-override.json yoodori-channel-v1 --yoodori-channel "${chat_channel:-off}"

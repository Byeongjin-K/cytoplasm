#!/usr/bin/env bash
# automation/memory_relocate/deploy.sh — retained recall and peer-choice configuration.
# The agent relocation schedule and wrapper were deleted on 2026-10-19.
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
  run_account "${account:-$NODE_AGENT_ACCOUNT}" "$script"
}

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $(printf '%q' "$account") -H bash -lc $(printf '%q' "$script")"
}

# push_file 은 원격 read-back 으로 착지를 확인한다 — 확인 없는 push 가 rc=0 으로
# 끝나면서 파일은 그대로였던 실측(2026-08-20)이 이 공유 구현의 이유다.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

# Deploy guard: refuse to deploy code that origin/main does not have.
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/memory_relocate" || exit 4

push_file "$repo_root/automation/memory_relocate/peer-mode.json" '.hermes/memory-relocate/peer-mode.json'

# Moved people facts are no longer in every prompt (owner decision Q138 ②): the agent's SOUL.md
# carries one recall line. Only that line is ensured; the rest of the owner-editable file stays.
source "$repo_root/automation/deploy_config.sh"
converge_config agent .hermes/SOUL.md hermes-soul-recall-v2

# Owner (2026-10-14 22:29): an approved release must need no node work. The choice mailboxes
# are therefore provisioned here, without root, by the accounts the release already runs as:
# ops creates the sticky shared directory in its existing hand-off directory, then each
# executor creates only its own mailbox. Re-runs only re-check; nothing is repaired or widened.
# -E -s (not -I): the module must import from the root-owned release tree it is started in.
release_root=/srv/autophagy-agent-current
module="cd $release_root && python3 -E -s -m automation.memory_relocate"
provision="$module.choice_accounts --provision"
run_account ops "$provision"
run_account "$NODE_AGENT_ACCOUNT" "$provision"
# This release explicitly retires peer deletion (peer-mode.json above). No peer
# provisioning or new executor registration; the old paused gateway job is retained.

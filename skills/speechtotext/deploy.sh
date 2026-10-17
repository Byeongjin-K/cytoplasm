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

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/skills/speechtotext/scripts/speechtotext_drive_watch.py" \
  "$repo_root/skills/speechtotext/deploy.sh" "$repo_root/skills/speechtotext/deploy-manifest.txt" || exit 4

push_file "$repo_root/skills/speechtotext/scripts/speechtotext_drive_watch.py" \
  '.hermes/scripts/speechtotext_drive_watch.py'

# 5분 틱: 한 번의 전사가 몇 시간 걸릴 수 있으므로 워처 자신이 flock 으로 겹침을 막는다.
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer speechtotext-drive-watch "*/5 * * * *" speechtotext_drive_watch.py local
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all'

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
deploy_provenance_check "$repo_root" "$repo_root/automation/state_backup" || exit 4

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u $NODE_AGENT_ACCOUNT -H bash -lc $(printf '%q' "$script")"
}

source "$repo_root/automation/deploy_push.sh"
push_file "$repo_root/automation/state_backup/cron/state_backup_watch.py" '.hermes/scripts/state_backup_watch.py'
run_agent 'grep -qx "timezone: Asia/Seoul" "$HOME/.hermes/config.yaml"'
# 암호화 키는 배포가 만들지 않는다 — 소유자가 노드에서 1회 생성하고 오프라인 사본을 둔다:
#   openssl rand -hex 32 > ~/.hermes/backup/backup.key && chmod 600 ~/.hermes/backup/backup.key
# 키가 없으면 워처는 BACKUP-KEY-MISSING 으로 fail-closed 한다(평문 업로드 없음).
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer state-backup-watch "15 3 * * *" state_backup_watch.py local

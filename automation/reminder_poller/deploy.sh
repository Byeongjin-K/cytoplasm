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
deploy_provenance_check "$repo_root" "$repo_root/automation/reminder_poller/poll_reminders.py" \
  "$repo_root/automation/reminder_poller/poller_core.py" \
  "$repo_root/automation/reminder_poller/reminder_store.py" \
  "$repo_root/automation/reminder_poller/deploy.sh" "$repo_root/automation/reminder_poller/deploy-manifest.txt" || exit 4

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "$script")"
}

source "$repo_root/automation/deploy_tree.sh"
source "$repo_root/automation/deploy_push.sh"
run_agent() { run_account "$NODE_AGENT_ACCOUNT" "$1"; }

# 알림 폴러는 cron sandbox가 찾는 계정 홈에만 복사해야 다른 계정의 상태를 읽지 않는다.
# 플랫 import 동반 파일 둘은 검증된 한 세대로 놓인 뒤 링크 .current 가 한 번에 넘어가고,
# 그다음에 래퍼를 보낸다. 옛 래퍼가 읽는 평면 파일은 쓰지도 옮기지도 않는다(retired 선언).
deploy_tree_swap --link "$repo_root/automation/reminder_poller" .hermes/reminder_poller_runtime/.current poller_core.py reminder_store.py || exit $?
push_file "$repo_root/automation/reminder_poller/poll_reminders.py" '.hermes/scripts/poll_reminders.py'
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer reminder-poller "*/5 * * * *" poll_reminders.py local

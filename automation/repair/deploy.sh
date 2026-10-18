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

runtime_files=(__init__.py ledger_policy.py ledger/__init__.py ledger/__main__.py ledger/store.py ledger/switch.py node_config.py notice_bot.py interop/__init__.py interop/chunker.py interop/discord_transport.py interop/report.py repair/__init__.py repair/repair_capability.py repair/repair_report_consumer.py repair/repair_report_queue.py repair/repair_report_send.py)
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/repair/cron/repair_report_consume_watch.py" \
  "${runtime_files[@]/#/$repo_root/automation/}" \
  "$repo_root/configs/node.example.toml" \
  "$repo_root/automation/repair/deploy.sh" "$repo_root/automation/repair/deploy-manifest.txt" || exit 4
source "$repo_root/automation/deploy_tree.sh"
source "$repo_root/automation/deploy_push.sh"

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "$script")"
}

run_agent() { run_account "$NODE_AGENT_ACCOUNT" "$1"; }

# 런타임은 래퍼보다 먼저 새 세대 링크로 한 번에 바뀐다. 루트의 옛 automation/ 은 옛 래퍼가 읽으므로 건드리지 않는다.
deploy_tree_swap --link --prefix automation "$repo_root/automation" .hermes/repair-report-runtime/.current __init__.py ledger_policy.py ledger/__init__.py ledger/__main__.py ledger/store.py ledger/switch.py node_config.py notice_bot.py interop/__init__.py interop/chunker.py interop/discord_transport.py interop/report.py repair/__init__.py repair/repair_capability.py repair/repair_report_consumer.py repair/repair_report_queue.py repair/repair_report_send.py || exit "$?"
push_file "$repo_root/configs/node.example.toml" .hermes/repair-report-runtime/.current/automation/node.example.toml
# 소비 감시는 cron sandbox가 찾는 계정 홈에만 복사해야 수리 런타임 권한을 넓히지 않는다.
push_file "$repo_root/automation/repair/cron/repair_report_consume_watch.py" '.hermes/scripts/repair_report_consume_watch.py'
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer repair-report-consumer "*/5 * * * *" repair_report_consume_watch.py local

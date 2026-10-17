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

# push_file 은 원격 read-back 으로 착지를 확인한다 — 확인 없는 push 가 rc=0 으로
# 끝나면서 파일은 그대로였던 실측(2026-08-20)이 이 공유 구현의 이유다.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/skills/budget/scripts/budget_watch.py" \
  "$repo_root/skills/mail/scripts/watch_failure_streak.py" \
  "$repo_root/skills/budget/deploy.sh" "$repo_root/skills/budget/deploy-manifest.txt" || exit 4

# 연속 실패 통지 헬퍼 — 워처들이 공유하는 사본 하나(`~/.hermes/scripts/`)이고 소스는
# skills/mail/scripts 에 산다(사본 신설 금지, 계획 CR-1). mail deploy.sh 와 같은 파일을
# 같은 목적지로 올리므로 어느 쪽을 먼저 돌려도 결과가 같다. 워처보다 **먼저** 올린다:
# 헬퍼 없이 착지한 워처도 돌긴 하지만(ImportError fallback) 옛 무통지 동작이 된다.
push_file "$repo_root/skills/mail/scripts/watch_failure_streak.py" \
  '.hermes/scripts/watch_failure_streak.py'
push_file "$repo_root/skills/budget/scripts/budget_watch.py" \
  '.hermes/scripts/budget_watch.py'
# Every ordinary failing tick is silent; only incident open/recovery notices reach Discord.
run_agent 'PATH="$HOME/.local/bin:$PATH"; job_id=$(hermes cron list --all | awk "/^  [0-9a-f]+ \[/{id=\$1} /Name:[[:space:]]+budget-watch\$/{print id; exit}"); if [ -n "$job_id" ]; then hermes cron edit "$job_id" --schedule "*/30 * * * *" --deliver discord --no-agent --script budget_watch.py; else hermes cron create "*/30 * * * *" --name budget-watch --no-agent --script budget_watch.py --deliver discord; fi'
source "$repo_root/automation/deploy_timer.sh"
converge_timer budget-watch "*/30 * * * *" budget_watch.py discord
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all'

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

# Deploy guard: refuse to push code that origin/main does not have (see the header of
# automation/deploy_provenance.sh for why a silent revert is otherwise inevitable).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/automation/notes_organize/notes_organize.py" \
  "$repo_root/skills/mail/scripts/watch_failure_streak.py" \
  "$repo_root/automation/notes_organize/deploy.sh" "$repo_root/automation/notes_organize/deploy-manifest.txt" || exit 4

# Ship the shared incident helper first. The wrapper keeps running if a partial deploy
# omits it, but notices cannot open or recover until this file is present.
push_file "$repo_root/skills/mail/scripts/watch_failure_streak.py" \
  '.hermes/scripts/watch_failure_streak.py'
push_file "$repo_root/automation/notes_organize/notes_organize.py" '.hermes/scripts/notes_organize.py'
run_agent 'grep -qx "timezone: Asia/Seoul" "$HOME/.hermes/config.yaml"'
# Weekdays provide catch-up after a failed Monday tick. The delivered-week watermark
# makes every later tick in a successfully consumed week a silent no-op.
run_agent 'PATH="$HOME/.local/bin:$PATH"; job_id=$(hermes cron list --all | awk "/^  [0-9a-f]+ \[/{id=\$1} /Name:[[:space:]]+notes-weekly-organize\$/{print id; exit}"); if [ -n "$job_id" ]; then hermes cron edit "$job_id" --schedule "0 8 * * 1-5" --deliver discord --no-agent --script notes_organize.py; else hermes cron create "0 8 * * 1-5" --name notes-weekly-organize --no-agent --script notes_organize.py --deliver discord; fi'
source "$repo_root/automation/deploy_timer.sh"
converge_timer notes-weekly-organize "0 8 * * 1-5" notes_organize.py discord
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all'

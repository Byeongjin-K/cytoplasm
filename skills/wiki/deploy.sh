#!/usr/bin/env bash
# Carry the wiki confirmation watcher to the node.
#
# WHY this exists (2026-08-20): `wiki-confirm-watch` is a cron entry that really runs on
# the node, but no deploy script carried `wiki_confirm_reaction_watch.py` — so every
# committed wiki fix stopped at the repository and the node kept running whatever was
# installed by hand. That is the `mail_triage_watch.py` failure again (108 dead ticks
# while the repo held the fix): 「커밋됨 ≠ 배포됨」 applies to watchers too.
#
# Shape follows skills/calendar/deploy.sh; the cron entry is created only when absent so
# a re-run never disturbs an existing job's id or history.
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
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "cd \"\$HOME\"; $script")"
}

# push_file 은 원격 read-back 으로 착지를 확인한다 — 확인 없는 push 가 rc=0 으로
# 끝나면서 파일은 그대로였던 실측(2026-08-20)이 이 공유 구현의 이유다.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

# Deploy guard: refuse to push code that origin/main does not have (see the header of
# automation/deploy_provenance.sh for why a silent revert is otherwise inevitable).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/skills/wiki/scripts/wiki_confirm_reaction_watch.py" \
  "$repo_root/skills/wiki/deploy.sh" "$repo_root/skills/wiki/deploy-manifest.txt" || exit 4

push_file "$repo_root/skills/wiki/scripts/wiki_confirm_reaction_watch.py" \
  '.hermes/scripts/wiki_confirm_reaction_watch.py'
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer wiki-confirm-watch "*/1 * * * *" wiki_confirm_reaction_watch.py local
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all'

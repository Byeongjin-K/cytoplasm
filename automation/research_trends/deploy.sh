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
# shellcheck source=automation/deploy_tree.sh
source "$repo_root/automation/deploy_tree.sh"

# Deploy guard: refuse to push code that origin/main does not have (see the header of
# automation/deploy_provenance.sh for why a silent revert is otherwise inevitable).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/automation/research_trends/research_trends.py" \
  "$repo_root/automation/research_trends/research_trends_core.py" \
  "$repo_root/automation/research_trends/topics_import.py" \
  "$repo_root/skills/mail/scripts/watch_failure_streak.py" \
  "$repo_root/prompts/research-trends-v1.md" \
  "$repo_root/automation/research_trends/deploy.sh" "$repo_root/automation/research_trends/deploy-manifest.txt" || exit 4

# 평면 런타임 사본은 실제 디렉터리로 두고 원자적으로 맞바꾼다(상시 프로브가 읽는 배치).
deploy_tree_swap "$repo_root/automation/research_trends" .hermes/research_trends_runtime research_trends.py research_trends_core.py topics_import.py
push_file "$repo_root/skills/mail/scripts/watch_failure_streak.py" \
  '.hermes/scripts/watch_failure_streak.py'
push_file "$repo_root/automation/research_trends/research_trends.py" '.hermes/scripts/research_trends.py'
push_file "$repo_root/prompts/research-trends-v1.md" '.hermes/research-trends/research-trends-v1.md'

# The weekly report moved outside cytoplasm. Keep the runtime available, but
# never register a schedule; retired_jobs removes the live timer in the release.

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

# 마운트 선행 조건 — 이 워처는 다른 워처와 달리 live 스킬을 subprocess 로 부르지 않고
# `sys.path` 에 넣어 **import** 한다. 스킬이 아직 마운트되지 않았는데 워처만 갱신하면 매 틱
# 기동 즉시 ImportError 로 죽고, 승인 ✅ 를 아무도 소비하지 않는 침묵이 된다(2026-08-21
# repair 승인 워처가 같은 모양으로 5일간 죽어 있었다). 순서를 산문이 아니라 여기서 강제한다.
# 필요한 모듈 목록은 워처 소스에서 도출하므로 import 가 늘어도 등록을 잊을 수 없다.
live_scripts='/srv/autophagy-skills/live/todo/scripts'
required_modules="$(python3 "$repo_root/automation/live_mount_preflight.py" \
  --watcher "$repo_root/skills/todo/scripts/todo_confirm_reaction_watch.py" \
  --scripts-dir "$repo_root/skills/todo/scripts" | tr '\n' ' ')"
missing_modules="$(run_agent "for m in $required_modules; do [ -f $live_scripts/\$m.py ] || echo \$m; done")"
if [ -n "$missing_modules" ]; then
  echo "DEPLOY-BLOCK: live 마운트에 워처가 import 하는 모듈이 없다: $(echo $missing_modules)" >&2
  echo "              스킬을 먼저 마운트하라 — automation/deploy-skill.sh todo (소유자 승인)." >&2
  echo "              워처를 먼저 배포하면 매 틱 ImportError 로 죽어 승인이 소비되지 않는다." >&2
  exit 5
fi

# push_file 은 원격 read-back 으로 착지를 확인한다 — 확인 없는 push 가 rc=0 으로
# 끝나면서 파일은 그대로였던 실측(2026-08-20)이 이 공유 구현의 이유다.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/skills/todo/scripts/todo_confirm_reaction_watch.py" \
  "$repo_root/skills/todo/deploy.sh" "$repo_root/skills/todo/deploy-manifest.txt" || exit 4

push_file "$repo_root/skills/todo/scripts/todo_confirm_reaction_watch.py" \
  '.hermes/scripts/todo_confirm_reaction_watch.py'
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer todo-confirm-watch "*/1 * * * *" todo_confirm_reaction_watch.py local
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all'

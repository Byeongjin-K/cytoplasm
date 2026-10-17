#!/usr/bin/env bash
# automation/project_board/deploy.sh — deploy the project board (v2) watchers and dashboard unit.
#
# The wrappers and the unit import automation.project_board.* from the runtime root
# (release tree, fallback ops checkout). Deploy = (1) provenance-check the package against
# origin/main, (2) push the two no-agent wrappers and the agent user unit, (3) idempotently
# register the two crons, (4) (re)start the loopback dashboard. The analysis cron stays
# silent until the node config sets analyze.enabled — a deploy never starts posting cards.
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

# Deploy guard: refuse to deploy code that origin/main does not have.
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/project_board" || exit 4

push_file "$repo_root/automation/project_board/cron/project_board_watch.py" '.hermes/scripts/project_board_watch.py'
push_file "$repo_root/automation/project_board/cron/project_board_analyze.py" '.hermes/scripts/project_board_analyze.py'
push_file "$repo_root/automation/project_board/systemd/autophagy-project-board.service" '.config/systemd/user/autophagy-project-board.service'

source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer project-board-watch "every 2m" project_board_watch.py local
converge_timer project-board-analyze "every 30m" project_board_analyze.py local

run_agent 'export XDG_RUNTIME_DIR="/run/user/$(id -u)"; systemctl --user daemon-reload && systemctl --user enable autophagy-project-board.service && systemctl --user restart autophagy-project-board.service && systemctl --user is-active autophagy-project-board.service'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all | grep -A3 project-board || true'

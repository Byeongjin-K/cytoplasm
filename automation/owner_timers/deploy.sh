#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" automation/owner_timers.py automation/owner_timers \
  automation/owner_timers_policy.py automation/cron_timer.py automation/release_approval_remote.sh automation/node_config.py
host="${DEPLOY_SSH_HOST:-$(PYTHONPATH="$repo_root" python3 -B -c 'from automation.node_config import load_node_config; print(load_node_config().deploy_ssh_host)')}"
[[ -n "$host" ]] || { echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset" >&2; exit 3; }
jobs="$(PYTHONPATH="$repo_root" python3 -B -m automation.owner_timers switch)" || exit 4
PYTHONPATH="$repo_root" python3 -B -m automation.owner_timers deploy --repo "$repo_root" \
  --host "$host" --head "$(git -C "$repo_root" rev-parse HEAD)" --jobs "$jobs"

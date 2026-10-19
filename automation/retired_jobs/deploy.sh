#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" automation/retired_jobs.py automation/retired_jobs \
  automation/gateway_retirement.py automation/gateway_retirement_policy.py \
  automation/node_config.py automation/node_config_sh.py
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"
if [[ -z "$host" ]]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset." >&2
  exit 3
fi
head="$(git -C "$repo_root" rev-parse HEAD)"
PYTHONPATH="$repo_root" python3 -B -m automation.retired_jobs deploy \
  --host "$host" --head "$head"

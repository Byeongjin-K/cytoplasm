#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/omo_relay" \
  "$repo_root/automation/omo_relay_release.py" "$repo_root/automation/node_config.py" \
  "$repo_root/automation/node_config_sh.py" "$repo_root/automation/release_approval_remote.sh" \
  "$repo_root/configs/node.example.toml" || exit 4
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
[[ -n "$host" ]] || exit 3
PYTHONPATH="$repo_root" python3 -B -m automation.omo_relay_release apply \
  --repo "$repo_root" --host "$host" --runtime "$NODE_RELEASE_CURRENT" \
  --head "$(git -C "$repo_root" rev-parse HEAD)"

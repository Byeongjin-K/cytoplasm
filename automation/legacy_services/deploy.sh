#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" automation/legacy_services.py automation/legacy_services \
  automation/release_spec.py automation/release_spec_message.py automation/node_config.py \
  automation/node_config_sh.py automation/release_approval_remote.sh
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"
if [[ -z "$host" ]]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  exit 3
fi
head="$(git -C "$repo_root" rev-parse HEAD)"
PYTHONPATH="$repo_root" python3 -B -m automation.legacy_services deploy \
  --repo "$repo_root" --host "$host" --head "$head"

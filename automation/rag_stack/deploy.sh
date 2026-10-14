#!/usr/bin/env bash
# Load the complete local execution closure BEFORE checking any deployed byte.
# The fixed function call below cannot dispatch unverified, late-loaded code.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${RAG_STACK_SSH_HOST:-$NODE_RAG_NODE_NAME}"
local_node="${RAG_STACK_LOCAL_NODE:-$(hostname -s)}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: RAG_STACK_SSH_HOST is unset and node config gave no RAG node name. Set it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi
source "$repo_root/automation/deploy_provenance.sh"
source "$repo_root/automation/runtime_package_probe.sh"
source "$repo_root/automation/rag_stack/deploy_runtime.sh"
deploy_provenance_check "$repo_root" "$repo_root/configs/rag" \
  "$repo_root/automation/rag_stack" "$repo_root/automation/rag_ingest/mcp_client.py" \
  "$repo_root/automation/deploy_tree_remote.sh" || exit 4
rag_stack_deploy "$@"

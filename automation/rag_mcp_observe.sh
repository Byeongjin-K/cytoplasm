#!/usr/bin/env bash
# Reuse the standing rag_stack_current probe against the release being approved/applied.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/healthcheck.sh" >&2 || exit 4
local_node="${RAG_STACK_LOCAL_NODE:-$(hostname -s)}"
if [[ "$NODE_RAG_NODE_NAME" == "$local_node" ]]; then
  # The probe already wraps both source snapshots and image inspection in
  # sudo -n -u ops -H. Keep those exact commands, replacing self-SSH only.
  capture_on_node() { timeout 45 bash -c "$2" < /dev/null; }
fi
HEALTHCHECK_RELEASE_SOURCE_ROOT="$1" probe_rag_stack_current \
  "$NODE_RAG_NODE_NAME" "$NODE_OPS_ACCOUNT" "$1/configs/runtime-package-manifest.txt"

#!/usr/bin/env bash
# Reuse the standing rag_stack_current probe against the release being approved/applied.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/healthcheck.sh" >&2 || exit 4
HEALTHCHECK_RELEASE_SOURCE_ROOT="$1" probe_rag_stack_current \
  "$NODE_RAG_NODE_NAME" "$NODE_OPS_ACCOUNT" "$1/configs/runtime-package-manifest.txt"

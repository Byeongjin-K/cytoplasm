#!/usr/bin/env bash
# Existing rag|tree source/image observation, plus an approval-bound extra release effect.
set -uo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
eval "$(python3 "$script_dir/node_config_sh.py" --print-env)" || exit 1

digest() {
  source "$script_dir/runtime_package_probe.sh"
  LC_ALL=C runtime_package_local_snapshot "$repo_root/configs/rag" tree | sha256sum | cut -d' ' -f1
}

observe() {
  local out rc=0
  read -r -a probe_cmd <<< "${RAG_MCP_PROBE_CMD:-bash $script_dir/rag_mcp_observe.sh}"
  out="$("${probe_cmd[@]}" "$repo_root" 2>&1)" || rc=$?
  printf '%s\n' "$out" >&2
  # The existing probe returns 1 for both stale and unknown. Never turn an unknown
  # (unreachable node, unreadable image, missing allowlist) into permission to mutate.
  if (( rc != 0 )) && [[ "$out" == *UNKNOWN* || "$out" != *RUNTIME-PACKAGE* ]]; then return 4; fi
  return "$rc"
}

[[ $# == 2 && "$2" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "$(git -C "$repo_root" rev-parse HEAD)" == "$2" ]] || exit 4
# Installations without a RAG node have no extra surface.
[[ -n "${NODE_RAG_NODE_NAME:-}" ]] || exit 0
case "$1" in
  plan)
    rc=0; observe || rc=$?
    case "$rc" in 0) ;; 1) digest ;; *) exit "$rc" ;; esac
    ;;
  refresh)
    rc=0; observe || rc=$?
    case "$rc" in
      0) printf 'RAG-MCP-REFRESH|current|-|-|current\n' ;;
      1) bash "$script_dir/rag_stack/deploy.sh" --release-approval "$2" "$(digest)" ;;
      *) printf 'RAG-MCP-REFRESH|failed|-|-|observation-unknown\n' ;;
    esac
    ;;
  *) exit 2 ;;
esac

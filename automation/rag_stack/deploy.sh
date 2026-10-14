#!/usr/bin/env bash
# Synchronize canonical personal-RAG sources. NEVER deletes or overwrites .env.secrets.
# Rebuild only MCP when the current release's owner approval covers its tree digest.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${RAG_STACK_SSH_HOST:-$NODE_RAG_NODE_NAME}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: RAG_STACK_SSH_HOST is unset and node config gave no RAG node name. Set it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/configs/rag" \
  "$repo_root/automation/rag_stack" "$repo_root/automation/rag_ingest/mcp_client.py" \
  "$repo_root/automation/deploy_tree_remote.sh" || exit 4
mode=sync
if (( $# )); then
  [[ $# == 3 && "$1" == --release-approval && "$2" =~ ^[0-9a-f]{40}$ && "$3" =~ ^[0-9a-f]{64}$ ]] || exit 2
  [[ "$(git -C "$repo_root" rev-parse HEAD)" == "$2" ]] || exit 4
  source "$repo_root/automation/runtime_package_probe.sh"
  expected="$(LC_ALL=C runtime_package_local_snapshot "$repo_root/configs/rag" tree | sha256sum | cut -d' ' -f1)"
  [[ "$expected" == "$3" ]] || exit 4
  read -r -a authorize <<< "${RAG_MCP_AUTHORIZE_CMD:-$repo_root/automation/release_approval_remote.sh}"
  rc=0
  "${authorize[@]}" authorize-rag-mcp --head "$2" --hash "$3" >/dev/null || rc=$?
  if (( rc != 0 )); then
    printf 'RAG-MCP-REFRESH|skipped|-|-|approval-unverified\n'
    exit 4
  fi
  mode=refresh
fi

# No payload or executable arrives through remote shell stdin. Source, helper and
# smoke files enter a fresh ops-owned staging directory, which is removed on exit.
stage="$(mktemp -d)"
remote=""
run_ops() { ssh -n "$host" "sudo -n -u ops -H bash -c $(printf '%q' "$1")"; }
cleanup() {
  [[ -z "$remote" ]] || run_ops "rm -rf -- '$remote'" || echo "RAG-MCP-CLEANUP-FAIL" >&2
  rm -rf -- "$stage"
}
trap cleanup EXIT
remote="$(run_ops 'cd "$HOME"; umask 077; mktemp -d /tmp/autophagy-rag-mcp.XXXXXX')"
[[ "$remote" =~ ^/tmp/autophagy-rag-mcp\.[A-Za-z0-9]+$ ]] || { remote=""; exit 5; }
# Archive uses the same explicit source selection as the existing source-only sync.
tar -C "$repo_root/configs/rag" \
  --exclude='.env.secrets' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.ruff_cache' --exclude='.pytest_cache' \
  -czf "$stage/source.tar.gz" compose.yaml personal-rag.service env.example mcp embedding
source "$repo_root/automation/runtime_package_probe.sh"
LC_ALL=C runtime_package_local_snapshot "$repo_root/configs/rag" tree > "$stage/expected"
scp -q "$stage/source.tar.gz" "$stage/expected" \
  "$repo_root/automation/rag_stack/remote.sh" "$repo_root/automation/rag_stack/smoke.py" \
  "$repo_root/automation/rag_ingest/mcp_client.py" \
  "$repo_root/automation/deploy_tree_remote.sh" "ops@$host:$remote/"
run_ops "bash '$remote/remote.sh' '$mode' '$remote'"

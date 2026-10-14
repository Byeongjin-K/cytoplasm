#!/usr/bin/env bash
# Function-only local execution closure, sourced before deploy.sh checks provenance.
# Synchronize canonical personal-RAG sources. NEVER deletes or overwrites .env.secrets.

run_ops() {
  if [[ "$NODE_RAG_NODE_NAME" == "$local_node" ]]; then
    sudo -n -u ops -H bash -c "$1" < /dev/null
  else
    ssh -n "$host" "sudo -n -u ops -H bash -c $(printf '%q' "$1")"
  fi
}

rag_stack_cleanup() {
  [[ -z "$remote" ]] || run_ops "rm -rf -- '$remote'" || echo "RAG-MCP-CLEANUP-FAIL" >&2
  rm -rf -- "$stage"
}

rag_stack_deploy() {
  mode=sync
  if (( $# )); then
    [[ $# == 3 && "$1" == --release-approval && "$2" =~ ^[0-9a-f]{40}$ && "$3" =~ ^[0-9a-f]{64}$ ]] || exit 2
    [[ "$(git -C "$repo_root" rev-parse HEAD)" == "$2" ]] || exit 4
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

  # No executable or data arrives through remote shell stdin. Both transfer
  # directories belong to this run and are removed on exit.
  stage="$(mktemp -d /tmp/autophagy-rag-transfer.XXXXXX)"
  remote=""
  trap rag_stack_cleanup EXIT
  remote="$(run_ops 'cd "$HOME"; umask 077; mktemp -d /tmp/autophagy-rag-mcp.XXXXXX')"
  [[ "$remote" =~ ^/tmp/autophagy-rag-mcp\.[A-Za-z0-9]+$ ]] || { remote=""; exit 5; }
  tar -C "$repo_root/configs/rag" \
    --exclude='.env.secrets' --exclude='.venv' --exclude='__pycache__' \
    --exclude='.ruff_cache' --exclude='.pytest_cache' \
    -czf "$stage/source.tar.gz" compose.yaml personal-rag.service env.example mcp embedding
  LC_ALL=C runtime_package_local_snapshot "$repo_root/configs/rag" tree > "$stage/expected"
  cp -- "$repo_root/automation/rag_stack/remote.sh" "$repo_root/automation/rag_stack/smoke.py" \
    "$repo_root/automation/rag_ingest/mcp_client.py" \
    "$repo_root/automation/deploy_tree_remote.sh" "$stage/"
  payload=("$stage/source.tar.gz" "$stage/expected" "$stage/remote.sh" \
    "$stage/smoke.py" "$stage/mcp_client.py" "$stage/deploy_tree_remote.sh")
  if [[ "$NODE_RAG_NODE_NAME" == "$local_node" ]]; then
    # Ops cannot traverse the caller's checkout/home. Expose only public source
    # files by name; no secret enters the transfer stage.
    chmod 711 "$stage"
    chmod 644 "${payload[@]}"
    sudo -n -u ops -H cp -- "${payload[@]}" "$remote/"
    sudo -n -u ops -H bash "$remote/remote.sh" "$mode" "$remote" < /dev/null
  else
    scp -q "${payload[@]}" "ops@$host:$remote/"
    run_ops "bash '$remote/remote.sh' '$mode' '$remote'"
  fi
}

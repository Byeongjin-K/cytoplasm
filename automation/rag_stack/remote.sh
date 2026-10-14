#!/usr/bin/env bash
# Runs as ops; a single lock covers source sync, image activation and rollback.
set -euo pipefail
mode="$1" stage="$2"
cd "$HOME"
umask 077
exec 9>>"$HOME/.personal-rag.deploy.lock"
flock -w 300 9 || exit 6
root="$HOME/personal-rag"
# renameat2 exchange needs the source and destination on the same filesystem.
incoming="$(mktemp -d "$HOME/.personal-rag.source.XXXXXX")"
trap 'rm -rf -- "$incoming"' EXIT
tar -xzf "$stage/source.tar.gz" -C "$incoming"
source "$stage/deploy_tree_remote.sh"
# Compare every managed file, not only a count or the two image modules.
expected_count="$(wc -l < "$stage/expected")"
remote_readback="$(_deploy_tree_snapshot "$incoming")"
[[ "$expected_count" -gt 1 && "$(printf 'SNAPSHOT-V1\n%s\n' "$remote_readback")" == "$( < "$stage/expected")" ]] \
  || { echo "RAG-STACK-DEPLOY-BLOCK: source read-back mismatch" >&2; exit 5; }
[[ -f "$root/.env.secrets" ]] || { echo "RAG-STACK-DEPLOY-BLOCK: node secrets missing" >&2; exit 5; }
# The node-only secret moves with the source tree, without changing its bytes or mode.
cp -p "$root/.env.secrets" "$incoming/.env.secrets"
_deploy_tree_rename exchange "$incoming" "$root"
remote_readback="$(_deploy_tree_snapshot "$root" | awk -F'|' '$2 != ".env.secrets"')"
if [[ "$(printf 'SNAPSHOT-V1\n%s\n' "$remote_readback")" != "$( < "$stage/expected")" ]]; then
  _deploy_tree_rename exchange "$incoming" "$root"
  echo "RAG-STACK-DEPLOY-BLOCK: installed source read-back mismatch" >&2
  exit 5
fi
if [[ "$mode" == sync ]]; then
  echo "RAG-STACK-DEPLOYED: source synchronized; .env.secrets preserved."
  exit 0
fi
[[ "$mode" == refresh ]] || exit 2
cd "$root"
export COMPOSE_PROJECT_NAME=personal_rag
compose=(docker compose --env-file .env.secrets -f compose.yaml)
image_before="-"; image_after="-"
result() { printf 'RAG-MCP-REFRESH|%s|%s|%s|%s\n' "$1" "$image_before" "$image_after" "$2"; }
cid="$("${compose[@]}" ps -q mcp)"
[[ -n "$cid" && "$cid" != *$'\n'* ]] || { result failed previous-container-unknown; exit 1; }
image_before="$(docker inspect --format '{{.Image}}' "$cid")"
[[ "$image_before" =~ ^sha256:[0-9a-f]{64}$ ]] || { image_before="-"; result failed previous-image-unknown; exit 1; }
previous_tag="autophagy-rag-mcp-rollback:${image_before#sha256:}"
docker image tag "$image_before" "$previous_tag"
printf 'services:\n  mcp:\n    image: %s\n' "$previous_tag" > "$stage/rollback.yaml"

smoke() { timeout 120 python3 "$stage/smoke.py" "$root/.env.secrets" "${RAG_MCP_HEALTH_SECONDS:-60}"; }
rollback() {
  # Override the image explicitly: --no-build/--no-deps also protect embedding.
  docker image tag "$image_before" "$previous_tag" \
    && "${compose[@]}" -f "$stage/rollback.yaml" up -d --no-build --pull never --no-deps mcp \
    && [[ "$(docker inspect --format '{{.Image}}' "$("${compose[@]}" ps -q mcp)")" == "$image_before" ]] \
    && smoke
}
if "${compose[@]}" up -d --build --no-deps mcp >&2; then
  cid="$("${compose[@]}" ps -q mcp)" || cid=""
  image_after="$(docker inspect --format '{{.Image}}' "$cid")" || image_after="-"
  if [[ "$image_after" =~ ^sha256:[0-9a-f]{64}$ ]] && smoke; then
    result installed verified
    exit 0
  fi
fi
if rollback; then
  result failed rolled-back
else
  result failed rollback-failed
fi
exit 1

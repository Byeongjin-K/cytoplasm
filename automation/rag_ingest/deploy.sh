#!/usr/bin/env bash
# automation/rag_ingest/deploy.sh — deploy the personal-RAG ingest runtime + watcher.
#
# Deploys the tracked rag_ingest *.py package (top-level cron/ excluded) to
# ~agent/.hermes/rag_ingest_runtime/rag_ingest/ with deploy_tree_swap, so the wrapper's
# `sys.path.insert(RUNTIME_DIR); from rag_ingest.cli import ...` and recall's search path
# resolve. cron/ is excluded because its wrapper has a separate, manifest-observed
# destination in the Hermes scripts directory. The destination is declared in
# deploy-manifest.txt, so a release can see a stale copy and re-ship it.
#
# The swap holds the watcher's own flock. A tick can therefore run before or after
# deployment, never while a partially extracted package is visible.
# Provenance enforces commit -> push -> deploy; origin/main must already contain it.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

# push_file performs a remote sha256 read-back for the separately deployed wrapper.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"
# shellcheck source=automation/deploy_tree.sh
source "$repo_root/automation/deploy_tree.sh"
# shellcheck source=automation/deploy_provenance.sh
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/rag_ingest" || exit 4

# The runtime stays a real directory: the standing runtime package probe snapshots it and
# recall imports from it on every search. deploy_tree_swap stages and verifies the tree,
# then swaps it in atomically under the watcher's own flock; a busy ingest returns rc 6
# after DEPLOY_TREE_LOCK_WAIT seconds (default 300) and leaves the live package untouched.
deploy_tree_swap --lock .hermes/rag-ingest/watch.lock "$repo_root/automation/rag_ingest" .hermes/rag_ingest_runtime/rag_ingest

push_file "$repo_root/automation/rag_ingest/cron/rag_ingest_watch.py" '.hermes/scripts/rag_ingest_watch.py'

# --all sees paused jobs too, so rerunning cannot create a duplicate cron entry.
source "$repo_root/automation/deploy_cron.sh"
source "$repo_root/automation/deploy_timer.sh"
converge_timer rag-ingest-watch "every 10m" rag_ingest_watch.py local
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all | grep -A3 rag-ingest-watch || true'

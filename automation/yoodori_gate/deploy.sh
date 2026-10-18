#!/usr/bin/env bash
# Release-owned forced command; never edits authorized_keys or grants a new SSH key.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [[ -z "$host" ]]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset" >&2
  exit 3
fi

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u $NODE_AGENT_ACCOUNT -H bash -c $(printf '%q' "set -eu; $script")"
}

source "$repo_root/automation/deploy_push.sh"
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/yoodori_gate" || exit 4

push_file "$repo_root/automation/yoodori_gate/gmail_read.py" '.local/libexec/gmail_read.py'
push_file "$repo_root/automation/yoodori_gate/yoodori_request_runtime.py" '.local/libexec/yoodori_request_runtime.py'
push_file "$repo_root/automation/yoodori_gate/yoodori_read_gate.py" '.local/libexec/yoodori-read-gate'
run_agent 'chmod 0755 "$HOME/.local/libexec/yoodori-read-gate"
test "$(stat -c %a "$HOME/.local/libexec/yoodori-read-gate")" = 755
test ! -L "$HOME/.local/share/yoodori-staging"
mkdir -p "$HOME/.local/share/yoodori-staging"
chmod 0700 "$HOME/.local/share/yoodori-staging"' </dev/null

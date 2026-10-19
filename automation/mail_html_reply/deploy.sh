#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" automation/mail_html_reply.py automation/mail_html_reply \
  automation/release_approval.py automation/release_approval_remote.sh automation/node_config.py \
  automation/node_config_sh.py
# Read only NODE_DEPLOY_SSH_HOST; this deployer keeps the closed grammar and evaluates nothing.
host="${DEPLOY_SSH_HOST:-}"
if [[ -z "$host" ]]; then
  host="$(python3 "$repo_root/automation/node_config_sh.py" --print-env | sed -n 's/^NODE_DEPLOY_SSH_HOST=//p')"
fi
if [[ -z "$host" ]]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  exit 3
fi
temporary="$(mktemp)"
if ! settings="$(PYTHONPATH="$repo_root" python3 -B -m automation.mail_html_reply prepare \
  --repo "$repo_root" --host "$host" --source "$temporary")"; then
  rm -f -- "$temporary"
  exit 4
fi
mapfile -t settings <<<"$settings"
host="${settings[0]}"
account="${settings[1]}"
run_agent() {
  ssh "$host" "sudo -n -u $account -H bash -c $(printf '%q' "$1")"
}
source "$repo_root/automation/deploy_push.sh"
rc=0
push_file "$temporary" '.hermes/mail-html-reply.mode' || rc=$?
rm -f -- "$temporary"
exit "$rc"

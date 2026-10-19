#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" automation/mail_html_reply.py automation/mail_html_reply \
  automation/release_approval.py automation/release_approval_remote.sh
mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.mail_html_reply switch)" || exit 4
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
[[ -n "$host" ]] || { echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset" >&2; exit 3; }
head="$(git -C "$repo_root" rev-parse HEAD)"
pointer="$(ssh "$host" "readlink /srv/autophagy-agent-current" </dev/null)" || exit 4
[[ "${pointer##*/}" == "$head" ]] || { echo "MAIL-HTML-REPLY-REFUSED: release HEAD differs" >&2; exit 4; }
if [[ "$mode" == on ]]; then
  "$repo_root/automation/release_approval_remote.sh" authorize-mail-html-reply --head "$head" \
    || { echo "MAIL-HTML-REPLY-UNAUTHORIZED: 같은 HEAD의 릴리스 승인이 필요합니다" >&2; exit 4; }
fi
run_agent() {
  ssh "$host" "sudo -n -u $NODE_AGENT_ACCOUNT -H bash -c $(printf '%q' "$1")"
}
source "$repo_root/automation/deploy_push.sh"
temporary="$(mktemp)"
trap 'rm -f -- "$temporary"' EXIT
printf '%s' "$mode" > "$temporary"
push_file "$temporary" '.hermes/mail-html-reply.mode'

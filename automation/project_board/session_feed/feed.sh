#!/usr/bin/env bash
# automation/project_board/session_feed/feed.sh — collect this workstation's 7-day session summary
# and copy it into the node's board root, where the dashboard's 세션 tab reads it on every request.
#
# The workstation pushes; the node opens nothing. The copy goes over the same SSH + `sudo -u <agent>`
# path the deploy scripts use, through deploy_push.sh's push_file (temporary file, sha256 read-back,
# rename), so the dashboard never sees a half-written file. Run it from a user timer every 10 minutes.
#
# Env (host values live in a private env file, never in this repository):
#   SESSION_FEED_SSH_HOST   the node, by an address on the private network (required)
#   SESSION_FEED_ACCOUNT    the account that runs the dashboard (default: agent)
#   SESSION_FEED_DEST       destination under that account's home (default: .hermes/project-board/sessions.json)
#   SB_*                    the collector's inputs (see collect.mjs)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
host="${SESSION_FEED_SSH_HOST:?SESSION_FEED_SSH_HOST is unset}"
account="${SESSION_FEED_ACCOUNT:-agent}"
dest="${SESSION_FEED_DEST:-.hermes/project-board/sessions.json}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
bun "$here/collect.mjs" "$work/sessions.json"

run_agent() {
  ssh -o BatchMode=yes -o ConnectTimeout=20 "$host" "sudo -n -u $account -H bash -c $(printf '%q' "$1")"
}
# shellcheck source=automation/deploy_push.sh
source "$here/../../deploy_push.sh"
push_file "$work/sessions.json" "$dest"
echo "SESSION-FEED sent $(wc -c < "$work/sessions.json") bytes"

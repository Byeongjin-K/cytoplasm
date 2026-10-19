#!/usr/bin/env bash
# automation/project_board/session_feed/feed.sh — collect this workstation's 7-day session summary
# and copy it into the node's board root, where the dashboard's 세션 tab reads it on every request.
#
# The workstation pushes; the node opens nothing. The copy goes over the same SSH + `sudo -u <agent>`
# path the deploy scripts use, through deploy_push.sh's push_file (temporary file, sha256 read-back,
# rename), so the dashboard never sees a half-written file. Run it from a user timer every 10 minutes.
#
# Peers: other accounts whose sessions belong on the same tab (another Dori on another machine). Each peer
# runs this same collect.mjs, sent on stdin, under its own account with its own inputs, frozen at the same
# SB_NOW, and its rows are added to this workstation's. A peer that fails is left out of that run, the rest
# is still sent, and the run exits 3 so the failure shows in the journal and on the tab's own service row.
#
# Env (host values live in a private env file, never in this repository):
#   SESSION_FEED_SSH_HOST   the node, by an address on the private network (required)
#   SESSION_FEED_ACCOUNT    the account that runs the dashboard (default: agent)
#   SESSION_FEED_DEST       destination under that account's home (default: .hermes/project-board/sessions.json)
#   SESSION_FEED_PEERS      optional space-separated peer names
#   SESSION_FEED_PEER_RUN   with peers: a command that runs argv as a peer, called as `<command> <peer> bash -c <script> <now>`;
#                           the peer keeps its SB_* inputs in ~/.config/session-feed/feed.env on its own account
#   SB_*                    the collector's inputs (see collect.mjs)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
host="${SESSION_FEED_SSH_HOST:?SESSION_FEED_SSH_HOST is unset}"
account="${SESSION_FEED_ACCOUNT:-agent}"
dest="${SESSION_FEED_DEST:-.hermes/project-board/sessions.json}"
read -ra peers <<< "${SESSION_FEED_PEERS:-}"
if (( ${#peers[@]} )); then read -ra peer_run <<< "${SESSION_FEED_PEER_RUN:?SESSION_FEED_PEER_RUN is unset}"; fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# One "now" for every collector, so the peers' rows share this workstation's seven-day window.
export SB_NOW="${SB_NOW:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
bun "$here/collect.mjs" "$work/local.json"

parts=("$work/local.json") failed=()
peer_script='set -euo pipefail
cd "$HOME"
set -a; . "$HOME/.config/session-feed/feed.env"; set +a
t="$(mktemp -d)"; trap '\''rm -rf "$t"'\'' EXIT
cat > "$t/collect.mjs"
SB_NOW="$0" bun "$t/collect.mjs" "$t/sessions.json" >&2
cat "$t/sessions.json"'
for peer in "${peers[@]}"; do
  if "${peer_run[@]}" "$peer" bash -c "$peer_script" "$SB_NOW" < "$here/collect.mjs" > "$work/peer-$peer.json" \
    && [[ -s "$work/peer-$peer.json" ]]; then
    parts+=("$work/peer-$peer.json")
  else
    echo "SESSION-FEED peer $peer failed; sending without it" >&2
    failed+=("$peer")
  fi
done

bun -e '
const [out, ...parts] = process.argv.slice(1);
const docs = await Promise.all(parts.map(p => Bun.file(p).json()));
const [first] = docs;
for (const d of docs) if (d.w0 !== first.w0 || d.now !== first.now) throw new Error("a peer used another window");
const join = k => docs.flatMap(d => d[k]);
await Bun.write(out, JSON.stringify({ w0: first.w0, now: first.now, lanes: join("lanes"), agents: join("agents"), aoe: join("aoe"), services: join("services") }));
' "$work/sessions.json" "${parts[@]}"

run_agent() {
  ssh -o BatchMode=yes -o ConnectTimeout=20 "$host" "sudo -n -u $account -H bash -c $(printf '%q' "$1")"
}
# shellcheck source=automation/deploy_push.sh
source "$here/../../deploy_push.sh"
push_file "$work/sessions.json" "$dest"
echo "SESSION-FEED sent $(wc -c < "$work/sessions.json") bytes from $(( ${#parts[@]} )) collector(s)"
if (( ${#failed[@]} )); then echo "SESSION-FEED missing peer(s): ${failed[*]}" >&2; exit 3; fi

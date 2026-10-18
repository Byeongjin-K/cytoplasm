#!/usr/bin/env bash
# Post-convergence validation for the release reconciler — and ONLY for it.
#
# `apply_release_update` installs a release, restarts the gateway pair, then runs this to
# decide whether to keep the new generation or roll it back. It runs as the reconciler
# does: `User=ops`, whose entire sudo grant is five `(root) NOPASSWD` lines for the
# release helpers. ops cannot become agent and cannot become peer.
#
# What used to sit here was `automation/deploy-smoke.sh`, which is a DIFFERENT thing: a
# standalone daily hygiene smoke with its own provisioner and its own timer
# (`provision-deploy-smoke.sh`, `autophagy-deploy-smoke.timer`), whose job is
# `deploy-skill.sh hello-autophagy --sandbox-only` — a real peer staging run. From ops
# that is not merely unlikely to pass, it is impossible: measured 2026-08-19,
# `sudo: a password is required` → `SELF-SKILL-COLLISION-BLOCK`, and ops had never
# written a `~/.hermes/deploy-smoke/tick.json` at all. Every convergence therefore built
# its release and was rolled back one step later. The two smokes are not merged: they
# answer different questions from different accounts.
#
# So this asks only what ops can actually answer, and each answer is one the reconciler
# would be wrong to skip:
#   1. the store agrees the pointer is the generation it just installed
#   2. the release tree IMPORTS — every cron watcher imports `automation.*` from it, so a
#      tree that cannot be imported is a node whose automation is silently dead
#   3. the gateway pair is HEALTHY, not merely restarted — the success path previously
#      took `systemctl restart` accepting the job as proof the pair came back
set -euo pipefail

readonly CURRENT="${RELEASE_SMOKE_CURRENT:-/srv/autophagy-agent-current}"
readonly GATEWAY_HELPER="${RELEASE_SMOKE_GATEWAY_HELPER:-/usr/local/libexec/autophagy-gateway-pair}"
# The one privileged call here. ops holds exactly one NOPASSWD rule for `… health`, so the
# elevation is named separately from the helper: tests point the helper at a stub and set
# this to empty rather than granting themselves sudo.
IFS=' ' read -r -a SUDO <<< "${RELEASE_SMOKE_SUDO-sudo -n}"

log() { printf '[release-smoke] %s\n' "$*" >&2; }
fail() { log "RELEASE-SMOKE-FAIL: $1"; exit 1; }

stopped=0
if (( $# )); then
  [[ $# == 1 && "$1" == --gateways-stopped ]] || fail "unknown smoke arguments"
  stopped=1
fi

resolved="$(readlink -e -- "$CURRENT")" || fail "release pointer does not resolve: $CURRENT"
[[ -d "$resolved" ]] || fail "release pointer is not a directory: $resolved"
sha="$(basename -- "$resolved")"
store_root="${RELEASE_SMOKE_STORE_ROOT:-$(dirname -- "$(dirname -- "$resolved")")}"

# 1. The store's own view of `current`. Run the JUST-INSTALLED release's copy, not the
#    privileged installed helper: this is a check on the generation we are validating.
PYTHONDONTWRITEBYTECODE=1 python3 -I "$resolved/automation/release_store.py" \
  current --verify "$sha" --store-root "$store_root" >/dev/null 2>&1 \
  || fail "the release store does not agree that current is $sha"

# 2. The tree the watchers load must be INTACT. Parsing is deliberate rather than
#    importing: half of `automation/*.py` does real work at import time (deploy_reconcile_cli
#    reads the node config on the module line), so importing everything would run the node
#    instead of checking it. `ast.parse` executes nothing, writes nothing, and still catches
#    the failure that matters here — a release whose files are truncated or corrupt, which
#    would leave every cron watcher dead with no other signal.
PYTHONDONTWRITEBYTECODE=1 python3 -I -c '
import ast, pathlib, sys

root = pathlib.Path(sys.argv[1]) / "automation"
broken = []
for path in sorted(root.rglob("*.py")):
    try:
        ast.parse(path.read_bytes(), filename=str(path))
    except (SyntaxError, OSError, ValueError) as error:
        broken.append(f"{path.relative_to(root)}: {type(error).__name__}")
if broken:
    print("; ".join(broken[:5]), file=sys.stderr)
    raise SystemExit(1)
if not list(root.rglob("*.py")):
    print("no python found under automation/", file=sys.stderr)
    raise SystemExit(1)
' "$resolved" || fail "the release tree is not intact — every cron watcher would be dead"

# 3. A previously applied stop must not be undone just to satisfy the health check.
# Live stopped-service verification remains deploy_all's responsibility.
if (( stopped )); then
  log "RELEASE-SMOKE-PASS: $sha (store agrees, tree parses, gateway stop policy retained)"
  exit 0
fi
"${SUDO[@]}" "$GATEWAY_HELPER" health >/dev/null 2>&1 \
  || fail "the gateway pair is not healthy after the restart"

log "RELEASE-SMOKE-PASS: $sha (store agrees, tree imports, gateway pair healthy)"

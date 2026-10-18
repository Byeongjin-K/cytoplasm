#!/usr/bin/env bash
# automation/checkout_mirror_probe.sh — is the ops deploy checkout still a clean
# one-way mirror of origin/main? Sourced by healthcheck.sh (the DETECT half) and
# by land.sh (the CONVERGE half), so both judge drift by one rule. land.sh runs
# from the workstation, so it ships these functions to the node BY VALUE
# (declare -f) rather than sourcing the node's copy: since DG-6 a dirty mirror
# only warns, and executing that mirror's shell as ops would take the warning's
# safety case away.
#
# WHY it is its own file: healthcheck.sh sits 5 lines under the 250 pure-LOC gate,
# and the verdict logic plus its two recovery texts do not fit. Extracting them
# keeps healthcheck.sh under the ceiling and hands land.sh the same primitive.
#
# WHY it exists at all, three dated faults:
#   2026-07-27  a commit made INSIDE /srv/autophagy-agents stranded work nowhere
#               else and blocked every session's ff-pull  → detected as mirror-ahead.
#   2026-07-29  the SSH probe was allowlist-denied (rc=126) AND the nested
#               `sudo -n -u ops` was sudoers-denied (rc=126) — TWO stacked denials
#               on one command, so the probe never ran. Fix: run LOCALLY on the
#               cron host (the checkout is local there), needing neither ssh nor sudo.
#   2026-07-29  ops sat 11 commits BEHIND origin after another session's push, and
#               the old probe compared against its own stale ref  → detected as
#               mirror-behind via `git ls-remote` (a network READ that writes no
#               local ref, so the read-only invariant holds: never fetch/pull/reset).
#   2026-09-01  three nonignored untracked files survived beside tracked edits,
#               proving that blanket `--untracked-files=no` hid real mirror work.

checkout_mirror_log() { printf '[checkout-mirror] %s\n' "$*" >&2; }

# Print exactly one verdict word; return 0 only for mirror-clean. Order matters:
# dirty and ahead are graver, offline, and certain; behind needs the network and
# is skipped (mirror-unknown-remote) rather than guessed when the remote is unreachable.
checkout_mirror_verdict() { # checkout_mirror_verdict <checkout-path>
  local checkout="$1" remote head
  [[ -d "$checkout/.git" ]] || { echo "mirror-no-checkout"; return 1; }
  if [[ -n "$(git -C "$checkout" status --porcelain --untracked-files=normal 2>/dev/null)" ]]; then
    echo "mirror-dirty"; return 1
  fi
  if ! git -C "$checkout" merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
    echo "mirror-ahead"; return 1
  fi
  remote="$(timeout 20 git -C "$checkout" ls-remote origin refs/heads/main 2>/dev/null | awk '{print $1}')"
  if [[ -z "$remote" ]]; then
    echo "mirror-unknown-remote"; return 1
  fi
  head="$(git -C "$checkout" rev-parse HEAD 2>/dev/null)"
  if [[ "$remote" != "$head" ]]; then
    echo "mirror-behind"; return 1
  fi
  echo "mirror-clean"
}

# Non-destructive recovery text, chosen by verdict. Ahead/dirty commits exist
# nowhere else, so their text must never invite a discard; behind just needs a pull.
checkout_mirror_guidance() { # checkout_mirror_guidance <checkout-path>
  case "$(checkout_mirror_verdict "$1")" in
    mirror-behind)
      cat <<'BEHIND_EOF'
The ops deploy checkout is behind origin/main. Whether that means prod is stale
depends on the node: with the immutable release live it is only this observation
post that lagged; with no release installed it IS prod running stale code.
Converge it non-destructively from the workstation:
  workstation$  automation/land.sh      # push + converge the runtime, verified
or, if nothing is unpushed, just:
  here$         git pull --ff-only
BEHIND_EOF
      ;;
    *)
      cat <<'AHEAD_EOF'
The ops deploy checkout is a one-way mirror of origin/main: the only writes
allowed inside it are git fetch and git pull --ff-only. This failure means a
commit was made there, or a tracked/nonignored untracked file was edited there.
Recover WITHOUT discarding the work - it exists nowhere else:
  here$         git format-patch origin/main..HEAD
  workstation$  git am *.patch && git push origin main
  here$         git pull --ff-only
Never git reset --hard, git checkout --, or git stash first.
AHEAD_EOF
      ;;
  esac
}


# ── grading (healthcheck only) ────────────────────────────────────────────────
# NOT part of the trio land.sh ships to the node by value (`declare -f` in its
# node_probe_script): a call to any of these from log/verdict/guidance would land there
# as "command not found". Keep the shipped three self-contained.

# The generation production is actually running, "" when there is none. Same rule as
# deploy_reconcile_cli.current_release_sha(): the release store names each generation by
# its origin/main sha, and absent OR dangling both read as "not converged" — treating a
# broken pointer as a satisfied one would go quiet exactly when the node is damaged.
checkout_mirror_release_sha() {
  local pointer="${RUNTIME_RELEASE_CURRENT:?RUNTIME_RELEASE_CURRENT must be resolved by the caller}" resolved
  resolved="$(readlink -e -- "$pointer" 2>/dev/null)" || return 0
  [[ -d "$resolved" ]] || return 0
  printf '%s\n' "${resolved##*/}"
}

# A behind mirror is graded by WHAT PRODUCTION RUNS, never by the mirror alone.
# Since DG-5 this checkout is an observation post: the reconcile timer converges the
# release from a DETACHED snapshot worktree and by design never moves this HEAD, so every
# landing left it behind while prod was exactly current — 447 failures measured between
# 2026-07-29 and 08-03, which is the cry-wolf this file exists to avoid. land.sh already
# grades that case a warning (LAND-MIRROR-WARN); this is the detect half agreeing.
#
# What must NOT be traded away for the quiet is the opposite case: there is no separate
# `release == origin/main` probe, so a stale release is visible only here. Hence the
# grade asks origin, not the mirror's own (possibly stale) ref.
# Is prod merely CATCHING UP rather than stuck? 0 = grace it, 1 = call it stale.
#
# The answer is not ours to invent: the reconciler already owns "a behind prod is now an
# incident" (DRIFT_NOTICE_SECONDS / FAILURE_NOTICE_THRESHOLD) and pages the owner on it.
# Deciding it a second time here is the same defect this file's behind-grading fixed on
# the land.sh side — two judges, one question, different answers — so we read its verdict
# instead of racing it. Measured: origin moves, the 2-minute timer converges the release,
# and a 5-minute healthcheck tick landing in that gap paged for a healthy node.
#
# Fail-closed in every direction: no file, unreadable, malformed, or a reconciler that has
# gone quiet for its own threshold all return 1. Silence must never buy silence — on
# 2026-08-02 the timer ran ~450 times in 15h without ever reaching save_state.
checkout_mirror_release_converging() {
  local state="${HEALTHCHECK_RECONCILE_STATE:?HEALTHCHECK_RECONCILE_STATE must be resolved by the caller}"
  local grace="${HEALTHCHECK_RECONCILE_GRACE:-600}" now written drift
  [[ -r "$state" ]] || return 1
  now="$(date +%s)"
  written="$(stat -c %Y "$state" 2>/dev/null)" || return 1
  (( now - written < grace )) || return 1
  drift="$(python3 -I -c 'import json,sys;v=json.load(open(sys.argv[1])).get("drift_since");print("" if v is None else int(v))' "$state" 2>/dev/null)" || return 1
  # No drift recorded: origin moved after the reconciler last looked, so this is at most
  # one tick old — the widest part of the window, not an edge case. The freshness check
  # above is what keeps "not yet seen" from meaning "never will be".
  [[ -n "$drift" ]] || return 0
  (( now - drift < grace ))
}

# Is origin/main ahead only by commits nobody has released yet? 0 = backlog, 1 = not proven.
#
# Since merges stopped cutting tags (VA-3), main runs ahead of the release between every
# merge and the next owner-approved release. The reconciler takes its backlog path only
# when the runtime already IS the latest signed release, records skip_reason=
# "release-backlog", and keeps drift_since at the episode start so its own 3-day digest
# can age. Reading that drift_since as convergence drift graded every backlog older than
# the grace `release-stale`: one repair card collected 711 such ticks, hours after each
# merge, for a node running exactly the release the owner approved. The same freshness
# rule as the grace applies — a file the timer stopped writing proves nothing.
checkout_mirror_release_backlog() {
  local state="${HEALTHCHECK_RECONCILE_STATE:?HEALTHCHECK_RECONCILE_STATE must be resolved by the caller}"
  local grace="${HEALTHCHECK_RECONCILE_GRACE:-600}" now written reason
  [[ -r "$state" ]] || return 1
  now="$(date +%s)"
  written="$(stat -c %Y "$state" 2>/dev/null)" || return 1
  (( now - written < grace )) || return 1
  reason="$(python3 -I -c 'import json,sys;v=json.load(open(sys.argv[1])).get("skip_reason");print(v if isinstance(v,str) else "")' "$state" 2>/dev/null)" || return 1
  [[ "$reason" == "release-backlog" ]]
}

checkout_mirror_behind_grade() { # checkout_mirror_behind_grade <checkout-path>
  local checkout="$1" release remote
  release="$(checkout_mirror_release_sha)"
  if [[ -z "$release" ]]; then
    checkout_mirror_log "mirror-behind: no release installed, so this checkout IS prod"
    return 1
  fi
  if [[ "${UPDATE_TRUST_BLOCK_REPORTED:-0}" == "1" ]]; then
    checkout_mirror_log "RELEASE-STALE-SUPPRESSED-WARN: signed update trust owns this tick's incident"
    return 0
  fi
  remote="$(timeout 20 git -C "$checkout" ls-remote origin refs/heads/main 2>/dev/null | awk '{print $1}')"
  if [[ -z "$remote" ]]; then
    checkout_mirror_log "BEHIND-UNKNOWN: origin unreachable while grading a behind mirror"
    return 0
  fi
  if [[ "$release" != "$remote" ]]; then
    if checkout_mirror_release_converging; then
      checkout_mirror_log "RELEASE-CONVERGING-WARN: prod is catching up to origin/main; the reconciler has not called it an incident"
      return 0
    fi
    if checkout_mirror_release_backlog; then
      checkout_mirror_log "RELEASE-BACKLOG-WARN: prod runs the latest release $release; origin/main $remote has unreleased commits"
      return 0
    fi
    checkout_mirror_log "release-stale: prod runs $release, origin/main is $remote"
    return 1
  fi
  checkout_mirror_log "MIRROR-BEHIND-WARN: the observation checkout lags; prod runs origin/main"
  return 0
}

# Judge the immutable release independently of mirror cleanliness. The second argument is
# set by healthcheck when checkout_mirrors_origin already emitted release-stale in this
# tick; suppressing here keeps one incident on one ticket path. Enforcement intentionally
# defaults to WARN until the owner promotes the rollout flag.
checkout_release_origin_grade() { # checkout_release_origin_grade <checkout-path> [mirror-reported-stale]
  local checkout="$1" mirror_reported_stale="${2:-0}" release remote
  release="$(checkout_mirror_release_sha)"
  if [[ -z "$release" ]]; then
    checkout_mirror_log "RELEASE-UNKNOWN: current release is absent or unreadable"
    return 1
  fi
  if [[ "${UPDATE_TRUST_BLOCK_REPORTED:-0}" == "1" ]]; then
    checkout_mirror_log "RELEASE-STALE-SUPPRESSED-WARN: signed update trust owns this tick's incident"
    return 0
  fi
  remote="$(timeout 20 git -C "$checkout" ls-remote origin refs/heads/main 2>/dev/null | awk '{print $1}')"
  if [[ -z "$remote" ]]; then
    checkout_mirror_log "BEHIND-UNKNOWN: origin unreachable while grading the release"
    return 0
  fi
  [[ "$release" != "$remote" ]] || return 0
  if [[ "$mirror_reported_stale" == "1" ]]; then
    checkout_mirror_log "RELEASE-STALE-SUPPRESSED-WARN: checkout mirror already owns this tick's release-stale incident"
    return 0
  fi
  if checkout_mirror_release_converging; then
    checkout_mirror_log "RELEASE-CONVERGING-WARN: prod is catching up to origin/main"
    return 0
  fi
  if checkout_mirror_release_backlog; then
    checkout_mirror_log "RELEASE-BACKLOG-WARN: prod runs the latest release $release; origin/main $remote has unreleased commits"
    return 0
  fi
  if [[ "${RELEASE_STALE_PROBE_ENFORCE:-0}" == "1" ]]; then
    checkout_mirror_log "release-stale: prod runs $release, origin/main is $remote"
    return 1
  fi
  checkout_mirror_log "RELEASE-STALE-WARN: prod runs $release, origin/main is $remote; enforcement disabled"
  return 0
}

probe_checkout_mirrors_origin() {
  local node="$1" account="$2" checkout="$3"
  local target="${HEALTHCHECK_OPS_CHECKOUT:-$checkout}"
  local output command_status=0
  valid_abs_path "$target" || return 1
  output="$(checkout_mirror_grade "$target" 2>&1)" || command_status=$?
  [[ -z "$output" ]] || printf '%s\n' "$output" >&2
  if (( command_status != 0 )) && [[ "$output" == *"release-stale:"* ]]; then
    RELEASE_STALE_REPORTED=1
  fi
  return "$command_status"
}

probe_release_matches_origin() {
  local node="$1" account="$2" checkout="$3"
  local target="${HEALTHCHECK_OPS_CHECKOUT:-$checkout}"
  valid_abs_path "$target" || return 1
  checkout_release_origin_grade "$target" "$RELEASE_STALE_REPORTED"
}

# Verdict -> pass/fail. Severe verdicts (dirty, ahead, no-checkout) are severe in every
# runtime mode: that work exists nowhere else, and a healthy release says nothing about it.
checkout_mirror_grade() { # checkout_mirror_grade <checkout-path>
  local checkout="$1" verdict
  verdict="$(checkout_mirror_verdict "$checkout")"
  case "$verdict" in
    mirror-clean) return 0 ;;
    mirror-unknown-remote)
      checkout_mirror_log "BEHIND-UNKNOWN: origin unreachable; ahead/dirty still checked"
      return 0 ;;
    mirror-behind) checkout_mirror_log "확인 MIRROR-BEHIND: 관측 미러 지연(서비스·릴리스는 별도 검사)"; return 0 ;;
    *) checkout_mirror_log "$verdict"; return 1 ;;
  esac
}

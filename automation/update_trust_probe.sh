#!/usr/bin/env bash

readonly UPDATE_TRUST_PROBE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

#: Ask the question CONVERGENCE asks, not the one node policy asks. `resolve` honours
#: `require_signed_updates`, so on a node carrying that opt-out this probe reported PASS
#: while every convergence tick was blocked — the same split-brain the reconciler was
#: cured of on 2026-08-21, one layer up. `resolve-signed` reads no configuration, so the
#: anti-rollback floor has to be named here: it is the same file both verifiers anchor to.
probe_update_trust() {
  local _node="$1" _account="$2" checkout="$3"
  local python="${UPDATE_TRUST_PYTHON:-python3}"
  local script="${UPDATE_TRUST_SCRIPT:-$UPDATE_TRUST_PROBE_DIR/update_trust.py}"
  local target="${HEALTHCHECK_OPS_CHECKOUT:-$checkout}" output command_status=0
  local floor="${HEALTHCHECK_RELEASE_FLOOR:-/var/lib/autophagy/update-trust/release-floor.json}"
  [[ "$target" == /* ]] || return 1
  if [[ "$floor" != /* ]]; then
    printf '[update-trust] UPDATE-TRUST-BLOCK FLOOR-PATH: cannot resolve the release floor\n' >&2
    return 1
  fi
  output="$("$python" "$script" resolve-signed --mirror "$target" --floor-path "$floor" 2>&1)" || command_status=$?
  if (( command_status == 0 )); then
    # A verified OLDER release while a newer one fails its signature is a frozen node,
    # not a healthy one: the resolver still answers rc 0 there (2026-10-16).
    local stale
    stale="$(grep -m1 '^UPDATE-TRUST-STALE-KEY' <<<"$output" || true)"
    [[ -z "$stale" ]] && return 0
    printf '[update-trust] %s\n' "$stale" >&2
    return 1
  fi
  if [[ "$output" == UPDATE-TRUST-BLOCK* ]]; then
    printf '[update-trust] %s\n' "$output" >&2
  else
    printf '[update-trust] UPDATE-TRUST-BLOCK %s\n' "$output" >&2
  fi
  return 1
}

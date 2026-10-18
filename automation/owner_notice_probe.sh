#!/usr/bin/env bash
# Read-only credential-decision detection. It answers ONE question — has anyone decided
# whether this node sends owner notices? — and never contacts Discord. Token validity,
# the Message Content intent, channel access and DM delivery are a different question,
# answered by automation/install/discord_check.py run once by the owner.
#
# The gateway's own token lives in the agent home (0600) and an ops probe cannot read it,
# so this file is deliberately about the notice path: reconcile failures and deploy drift.
if [[ -z "${HEALTHCHECK_OWNER_NOTICE_PROBE_LOADED:-}" ]]; then
  readonly HEALTHCHECK_OWNER_NOTICE_PROBE_LOADED=1
  readonly OWNER_NOTICE_CREDENTIAL_PATH="${HEALTHCHECK_OWNER_NOTICE_CREDENTIAL:-/etc/autophagy/repair-approval.env}"
  readonly -a OWNER_NOTICE_REQUIRED_KEYS=(DISCORD_BOT_TOKEN AUTOPHAGY_OWNER_ID)
  readonly OWNER_NOTICE_OPT_OUT_KEY="OWNER_NOTICE_OPTIONAL"
fi

# The judged path is an argument, never re-derived: a recovery line that names a file the
# probe did not read sends the operator to fix the wrong one.
owner_notice_credentials_guidance() { # owner_notice_credentials_guidance [path]
  printf 'OWNER-NOTICE-CREDENTIAL-RECOVERY: OWNER decide once in %s (root:ops 0640) — either fill %s, or declare %s=1 to run this node without owner notices.\n' \
    "${1:-$OWNER_NOTICE_CREDENTIAL_PATH}" "${OWNER_NOTICE_REQUIRED_KEYS[*]}" "$OWNER_NOTICE_OPT_OUT_KEY"
  printf '%s\n' \
    'OWNER-NOTICE-CREDENTIAL-RECOVERY: until one of them is written this node cannot tell the owner that convergence failed or that a deployment drifted — the sweep keeps reporting ALL_HEALTHY while the notice never arrives.' \
    'OWNER-NOTICE-CREDENTIAL-RECOVERY: this probe reads one file and contacts nothing. Token validity, the Message Content intent, channel access and DM delivery are confirmed separately — docs/guide/install.md §5 (automation/install/discord_check.py), run once on this node.'
}

# systemd EnvironmentFile semantics: a later assignment replaces an earlier one, so the
# LAST occurrence decides. Reading "any non-empty" would call a credential present that
# the runtime sees as empty. Values are compared, never printed.
_owner_notice_value_present() { # _owner_notice_value_present <path> <key>
  local path="$1" key="$2" line value last=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ "$line" == "$key="* ]] || continue
    value="${line#"$key="}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value#[\"\']}"
    value="${value%[\"\']}"
    last="$value"
  done < "$path"
  [[ -n "$last" ]]
}

probe_owner_notice_credentials() { # probe_owner_notice_credentials <node> <account> <path>
  local path="${3:-$OWNER_NOTICE_CREDENTIAL_PATH}" key
  local -a missing=()
  local runtime
  runtime="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  PYTHONPATH="$runtime" python3 -B -m automation.yoodori_channel --override-status || return 1
  if [[ ! -e "$path" ]]; then
    printf '%s\n' '[healthcheck] OWNER-NOTICE-CREDENTIAL-ABSENT'
    owner_notice_credentials_guidance "$path"
    return 1
  fi
  if [[ ! -f "$path" || ! -r "$path" ]]; then
    printf '%s\n' '[healthcheck] OWNER-NOTICE-CREDENTIAL-UNREADABLE'
    owner_notice_credentials_guidance "$path"
    return 1
  fi
  if _owner_notice_value_present "$path" "$OWNER_NOTICE_OPT_OUT_KEY"; then
    printf '%s\n' '[healthcheck] OWNER-NOTICE-CREDENTIAL-DECLARED-OPTIONAL'
    return 0
  fi
  for key in "${OWNER_NOTICE_REQUIRED_KEYS[@]}"; do
    _owner_notice_value_present "$path" "$key" || missing+=("$key")
  done
  if (( ${#missing[@]} > 0 )); then
    # Key NAMES only: this line becomes a repair ticket body and a sweep log entry.
    printf '[healthcheck] OWNER-NOTICE-CREDENTIAL-MISSING %s\n' "${missing[*]}"
    owner_notice_credentials_guidance "$path"
    return 1
  fi
  printf '%s\n' '[healthcheck] OWNER-NOTICE-CREDENTIAL-PASS'
}

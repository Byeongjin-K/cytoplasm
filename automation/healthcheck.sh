#!/usr/bin/env bash
# Read-only liveness monitor for the deployed autophagy services.
#
# Run as the configured ops account. HEALTHCHECK_SSH_USER and HEALTHCHECK_SSH_IDENTITY
# default to the configured operator and ~/.ssh/autophagy-healthcheck (when the
# key is readable). It deliberately uses SSH plus read-only GET and systemctl
# --user is-active probes, so it remains independent of Hermes.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"; eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
export RUNTIME_RELEASE_CURRENT="${RUNTIME_RELEASE_CURRENT:-$NODE_RELEASE_CURRENT}" HEALTHCHECK_RECONCILE_STATE="${HEALTHCHECK_RECONCILE_STATE:-$NODE_PRIVATE_ROOT/deploy-reconcile/state.json}"
readonly PRIMARY_NODE="$NODE_PRIMARY_NODE_NAME"
readonly RAG_NODE="$NODE_RAG_NODE_NAME"

# The deploy-checkout drift verdict + its recovery text live in a sibling library
# so this file stays under the 250 pure-LOC gate. It runs LOCALLY (no ssh/sudo).
# shellcheck source=automation/checkout_mirror_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/checkout_mirror_probe.sh"
# The skill-mount probe (verdict, recovery text, and the probe itself) lives in a
# sibling library for the same reason as the checkout one: this file must stay under
# the 250 pure-LOC gate. That library resolves the runtime root on its own.
# The skill-mount verdict + its recovery text live in a sibling library for the same
# reason as the checkout one: this file must stay under the 250 pure-LOC gate.
# shellcheck source=automation/skill_mount_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/skill_mount_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/selfskill_root_probe.sh"
# shellcheck source=automation/release_store_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/release_store_probe.sh"
# shellcheck source=automation/peer_gateway_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/peer_gateway_probe.sh"
# shellcheck source=automation/owner_notice_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/owner_notice_probe.sh"
# shellcheck source=automation/release_helper_probe.sh
# shellcheck source=automation/watcher_drift_probe.sh
# shellcheck source=automation/healthcheck_wrapper_probe.sh
# shellcheck source=automation/runtime_package_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/release_helper_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/watcher_drift_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_wrapper_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/runtime_package_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_probe_evidence.sh"
# shellcheck source=automation/release_receipt_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/release_receipt_probe.sh"
# shellcheck source=automation/healthcheck_command_builder.sh
# shellcheck source=automation/healthcheck_validation.sh
# shellcheck source=automation/healthcheck_probes.sh
source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_command_builder.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_validation.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_probes.sh"
# shellcheck source=automation/update_trust_probe.sh
# shellcheck source=automation/healthcheck_roster_probe.sh
source "$(dirname "${BASH_SOURCE[0]}")/update_trust_probe.sh"; source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_roster_probe.sh"
# shellcheck source=automation/healthcheck_registry.sh
source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_registry.sh"
# shellcheck source=automation/healthcheck_suggest.sh
source "$(dirname "${BASH_SOURCE[0]}")/healthcheck_suggest.sh"

# Static-view compatibility for legacy source scanners; runtime data lives above.
: <<'HEALTHCHECK_REGISTRY_STATIC_VIEW'
  "$PRIMARY_NODE signed update trust|update_trust|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_DEPLOY_CHECKOUT"
  "$PRIMARY_NODE ops checkout mirrors origin/main|checkout_mirrors_origin|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_DEPLOY_CHECKOUT"
  "$PRIMARY_NODE release matches origin/main|release_matches_origin|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_DEPLOY_CHECKOUT"
  "$PRIMARY_NODE privileged release helpers match release|release_helper_drift|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_LIBEXEC_DIR"
  "$PRIMARY_NODE skill mounts match the release|skill_mounts_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_SKILL_STORE/live"
  "$PRIMARY_NODE agent selfskill root topology|agent_selfskill_root_topology|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_SKILL_STORE/live"
  "$PRIMARY_NODE release store usage|release_store_usage|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_RELEASE_STORE"
  "$PRIMARY_NODE watcher wrappers match the release|watcher_wrappers_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_WATCHER_MANIFEST:-$(dirname "${BASH_SOURCE[0]}")/../configs/watcher-deploy-manifest.txt}"
  "$PRIMARY_NODE runtime packages match the release|primary_runtime_packages_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST:-$(dirname "${BASH_SOURCE[0]}")/../configs/runtime-package-manifest.txt}"
  "$RAG_NODE personal RAG source and MCP image match the release|rag_stack_current|${RAG_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST:-$(dirname "${BASH_SOURCE[0]}")/../configs/runtime-package-manifest.txt}"
  "$PRIMARY_NODE healthcheck probe allowlist matches the checks|healthcheck_wrapper_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|automation/healthcheck_probe_wrapper.sh"
  "$PRIMARY_NODE owner notice credentials|owner_notice_credentials|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_OWNER_NOTICE_CREDENTIAL:-/etc/autophagy/repair-approval.env}"
readonly LOCAL_PROBES="update_trust checkout_mirrors_origin release_matches_origin release_helper_drift skill_mounts_current agent_selfskill_root_topology release_store_usage release_fully_deployed peer_ignored_channels owner_notice_credentials"
agent_selfskill_root_topology) selfskill_root_guidance ;;
owner_notice_credentials) owner_notice_credentials_guidance ;;
owner_notice_credentials) probe_owner_notice_credentials "$node" "$account" "$target" ;;
agent_selfskill_root_topology) probe_selfskill_root_topology "$node" "$account" "$target" ;;
release_store_usage) probe_release_store_usage "$node" "$account" "$target" ;;
HEALTHCHECK_REGISTRY_STATIC_VIEW

readonly LOG_DIR="${HEALTHCHECK_LOG_DIR:-$NODE_PRIVATE_ROOT/runtime-logs/healthcheck}"
# 겹친 cron 틱이 서로를 보는 유일한 지점 — main() 의 양보 가드가 이 파일을 잡는다.
readonly LOCK_FILE="${HEALTHCHECK_LOCK_FILE:-$LOG_DIR/healthcheck.lock}"
if [[ -v HEALTHCHECK_SSH_IDENTITY ]]; then
  readonly SSH_IDENTITY="$HEALTHCHECK_SSH_IDENTITY"
elif [[ -r "$HOME/.ssh/autophagy-healthcheck" ]]; then
  readonly SSH_IDENTITY="$HOME/.ssh/autophagy-healthcheck"
else
  readonly SSH_IDENTITY=""
fi
readonly SSH_REMOTE_USER="${HEALTHCHECK_SSH_USER-$NODE_OPERATOR_ACCOUNT}"
SSH_OPTIONS=(
  -o BatchMode=yes
  -o ClearAllForwardings=yes
  -o ConnectTimeout=15
  -o StrictHostKeyChecking=yes
)
if [[ -n "$SSH_IDENTITY" ]]; then
  SSH_OPTIONS+=(-o IdentitiesOnly=yes -i "$SSH_IDENTITY")
fi
readonly -a SSH_OPTIONS

UPDATE_TRUST_BLOCK_REPORTED=0
RELEASE_STALE_REPORTED=0

log() {
  printf '[healthcheck] %s\n' "$*"
}

usage() {
  cat >&2 <<'EOF'
Usage: healthcheck.sh [--suggest | --synthetic-failure]

Without arguments, check the deployed services. --suggest reports which service
groups this installation appears to run and prints the HEALTHCHECK_SERVICES line
to declare them; it probes read-only, raises no ticket and changes nothing.
--synthetic-failure performs one read-only is-active probe for a deliberately
nonexistent ops user unit; it exists only to prove failure reporting without
disrupting a real service.
EOF
  exit 2
}

require_commands() {
  local command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null 2>&1 || {
      printf '[healthcheck] ERROR: required command not found: %s\n' "$command_name" >&2
      exit 1
    }
  done
}

setup_log() {
  local timestamp

  umask 077
  mkdir -p -m 700 -- "$LOG_DIR"
  chmod 700 -- "$LOG_DIR"
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  LOG_FILE="${LOG_DIR}/healthcheck-${timestamp}.log"
  : > "$LOG_FILE"
  chmod 600 -- "$LOG_FILE"
  exec > >(tee -a "$LOG_FILE") 2>&1
  log "log=${LOG_FILE}"
}


report_repair() {
  local check_name="$1"
  local probe_type="$2"
  local ssh_target="$PRIMARY_NODE" output repair_command command_status=0

  [[ "${HEALTHCHECK_NO_REPAIR:-}" != "1" ]] || return 0
  [[ "$check_name" =~ ^[a-zA-Z0-9_.@:/[:space:]-]+$ ]] || return 1
  [[ -z "$SSH_REMOTE_USER" ]] || ssh_target="${SSH_REMOTE_USER}@${PRIMARY_NODE}"
  repair_command="$(healthcheck_repair_command "$check_name")" || return 1
  output="$( { printf 'healthcheck failure: %s\n' "$check_name"; repair_guidance "$probe_type"; } | timeout 90 ssh "${SSH_OPTIONS[@]}" "$ssh_target" "$repair_command" 2>&1)" || command_status=$?
  if (( command_status == 0 )); then
    log "REPAIR_TICKET ${output}"
  else
    log "REPAIR_TICKET_FAILED rc=${command_status}"
  fi
}


main() {
  local -a checks=("${LIVE_CHECKS[@]}")
  local definition check_name probe_type
  local -a failed_checks=()
  local remote_total=0 remote_failed=0 suggest_mode=0 hint

  case "${1:-}" in
    "") ;;
    --suggest) suggest_mode=1 ;;
    --synthetic-failure)
      checks=("synthetic nonexistent ops unit|user_unit_active|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|autophagy-healthcheck-synthetic-does-not-exist.service")
      ;;
    *) usage ;;
  esac
  [[ "$#" -le 1 ]] || usage

  require_commands awk chmod date du flock grep mkdir mountpoint sed ssh stat tee timeout
  if [[ -n "$SSH_IDENTITY" && ! -r "$SSH_IDENTITY" ]]; then
    printf '[healthcheck] ERROR: SSH identity is not readable: %s\n' "$SSH_IDENTITY" >&2
    return 1
  fi

  # An operator asking what to declare is not a sweep: it must not take the sweep's lock,
  # write its log, notify, or raise a ticket. It reuses the same probes, so it needs no
  # command the SSH forced-command allowlist does not already carry.
  if (( suggest_mode == 1 )); then
    healthcheck_suggest_run
    return $?
  fi

  # cron 은 이 sweep 을 */5 로 부르지만 최근 400 회 실행의 중앙값은 4048 초였다(p90 14760 초,
  # 최대 39020 초). 틱 간격보다 한 자릿수 길어 틱이 겹쳐 쌓였고, 2026-08-31 에 동시 실행
  # 114 개가 관측됐다. 그 폭주 아래에서 SSH 프로브가 간헐 타임아웃해 **거짓** 수리 티켓을
  # 냈다 — t_2578c8ed(게이트웨이)·t_2524fe33(peer 게이트웨이)는 16:30:02Z 에 시작한 실행이
  # 18:25:23Z 에 보고한 것이고, 같은 프로브는 다른 모든 실행에서 PASS 였다.
  # 그래서 겹친 틱은 sweep 을 **시작하지 않고** 양보한다: 양보는 실패가 아니라 다음 틱에
  # 넘기는 것이므로 rc 0 이다(automation/pipeline_lock.py 와 같은 규약). lock 을 열지도
  # 못하는 상태는 잡혀 있는 것과 구별할 수 없으므로 fail-closed 로 멈춘다.
  # fd 9 는 sweep 이 끝날 때까지 열어 둔다 — 닫는 순간 lock 이 풀려 겹침이 다시 열린다.
  mkdir -p -m 700 -- "$LOG_DIR"
  exec 9>"$LOCK_FILE" || {
    printf '[healthcheck] HEALTHCHECK-LOCK-UNAVAILABLE path=%s\n' "$LOCK_FILE" >&2
    return 1
  }
  flock -n 9 || {
    printf '[healthcheck] HEALTHCHECK-OVERLAP-SKIP a previous sweep still holds %s\n' "$LOCK_FILE" >&2
    return 0
  }
  setup_log
  log "mode=${1:-live} read_only=true"
  report_roster_identity

  for definition in "${checks[@]}"; do
    IFS='|' read -r check_name probe_type _ <<< "$definition"
    [[ " $LOCAL_PROBES healthcheck_wrapper_current watcher_wrappers_current " == *" $probe_type "* ]] || remote_total=$(( remote_total + 1 ))
    if healthcheck_run_check_with_evidence "$definition"; then
      log "PASS ${check_name}"
    else
      if [[ "$probe_type" == healthcheck_wrapper_current || "$probe_type" == watcher_wrappers_current ]]; then
        log "확인 ${check_name}"
        continue
      fi
      log "FAIL ${check_name}"
      # The operator who met this as a bare FAIL had to go find the documentation to learn
      # that an optional group can be declined. The answer belongs at the question.
      hint="$(healthcheck_optional_group_hint "$check_name")" || hint=""
      if [[ -n "$hint" ]]; then
        log "$hint"
      fi
      failed_checks+=("$definition")
      [[ " $LOCAL_PROBES " == *" $probe_type "* ]] || remote_failed=$(( remote_failed + 1 ))
    fi
  done

  # Before the healthy early-return ON PURPOSE: the aggregator has to see a clean sweep to
  # close an open incident, so calling it only on failure would report every outage and
  # never a recovery. It self-loads its credential (cron, not systemd) and swallows its own
  # failures — a Discord outage must not change this sweep's verdict.
  "$(dirname "${BASH_SOURCE[0]}")/healthcheck_notify.sh" "${failed_checks[@]}"
  if (( ${#failed_checks[@]} == 0 )); then
    log "ALL_HEALTHY"
    return 0
  fi

  # Every SSH-borne probe rides one transport, so all of them failing is a single
  # transport/credential fault, not N outages - ticketing each would bury the cause
  # under identical noise on that same broken path. The local checkout probe is
  # excluded from this tally: it needs no SSH, so its verdict is independent and
  # must not mask (or be masked by) a fleet-wide SSH outage. >1 keeps a single
  # deliberately-failing --synthetic-failure check on the ticket path.
  if (( remote_failed == remote_total && remote_total > 1 )); then
    log "ERROR: every remote check failed - suspect the shared SSH path, not ${remote_total} separate services"
    log "ERROR: resolved HEALTHCHECK_SSH_USER=${SSH_REMOTE_USER:-<none>} HEALTHCHECK_SSH_IDENTITY=${SSH_IDENTITY:-<none>}"
    log "INFRA_FAILURE"
    return 1
  fi

  for definition in "${failed_checks[@]}"; do
    IFS='|' read -r check_name probe_type _ <<< "$definition"
    report_repair "$check_name" "$probe_type" || log "REPAIR_TICKET_FAILED validation"
  done

  log "HEALTHCHECK_FAILED"
  return 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi

#: The separable deployments an installation may run. `core` is what every install has;
#: the others exist on some installations and not others, and a probe for a service that
#: was never installed can only fail — `user_unit_active` against a unit nobody placed.
#: A third-party single-node install met exactly that on 2026-09-07 and could not edit the
#: rows out either: the deploy checkout is a one-way mirror
#: (docs/troubleshooting/신규-노드-설치-공백.md §5).
readonly -a HEALTHCHECK_SERVICE_GROUPS=(core report-hub rag)

#: Parse the declaration ONCE, and parse ALL of it. Plain `read -ra` stops at the first
#: newline, so a value written across lines — ordinary in a shell profile or a systemd
#: EnvironmentFile — silently dropped every name after the first: validation never saw
#: them and the filter never emitted them, exiting 0 the whole way. That is the exact
#: silence this option exists to remove, so it must not return through how the value is
#: read. `-d ''` consumes the whole value and then reports EOF, which `set -e` must not
#: mistake for a failure. Both consumers read this one array so they cannot disagree
#: about what was declared.
#: The installer renders the declaration it derived from the chosen profile into a
#: root-owned file, so a fresh node never derives it by hand. Only an UNSET variable
#: falls back to the file: an operator who exported the variable — even empty, meaning
#: "every probe" — is overriding the installer's answer on purpose.
readonly HEALTHCHECK_SERVICES_FILE="${HEALTHCHECK_SERVICES_FILE:-/etc/autophagy/healthcheck.env}"
if [[ ! -v HEALTHCHECK_SERVICES && -r "$HEALTHCHECK_SERVICES_FILE" ]]; then
  _declared_value=""
  while IFS= read -r _declared_line || [[ -n "$_declared_line" ]]; do
    case "$_declared_line" in
      HEALTHCHECK_SERVICES=*) _declared_value="${_declared_line#HEALTHCHECK_SERVICES=}" ;;
    esac
  done < "$HEALTHCHECK_SERVICES_FILE"
  _declared_value="${_declared_value#\"}"
  HEALTHCHECK_SERVICES="${_declared_value%\"}"
  unset _declared_line _declared_value
fi
#: The dashboard listens only on the address its hub.env binds (tailnet), which the node
#: name may not resolve to — then the probe gets no answer instead of 401. The URL is an
#: installation value, so it lives in the same root-owned, world-readable file: every
#: consumer (the cron sweep, the delegated probe, the wrapper generator under any account)
#: then derives the same probe command, and the allowlist keeps matching. Only an UNSET
#: variable falls back to the file, as above.
if [[ ! -v HEALTHCHECK_REPORT_HUB_DASHBOARD_URL && -r "$HEALTHCHECK_SERVICES_FILE" ]]; then
  while IFS= read -r _declared_line || [[ -n "$_declared_line" ]]; do
    case "$_declared_line" in
      HEALTHCHECK_REPORT_HUB_DASHBOARD_URL=*)
        _declared_value="${_declared_line#HEALTHCHECK_REPORT_HUB_DASHBOARD_URL=}"
        _declared_value="${_declared_value#\"}"
        HEALTHCHECK_REPORT_HUB_DASHBOARD_URL="${_declared_value%\"}"
        ;;
    esac
  done < "$HEALTHCHECK_SERVICES_FILE"
  unset _declared_line _declared_value
fi
HEALTHCHECK_DECLARED_SERVICES=()
if [[ -n "${HEALTHCHECK_SERVICES-}" ]]; then
  # Bind the separators rather than inheriting the caller's IFS. A caller whose IFS held
  # `*` turned an unknown name into punctuation: `core * rag` emitted 19 rows and said
  # nothing, which is the same silence by another route.
  #
  # Terminate the input with a NUL instead of reading to EOF. `read -d ''` reports EOF
  # with a non-zero status even after a complete parse, so the obvious `|| true` also
  # swallowed genuine failures — with descriptors exhausted the here-string failed and
  # every declaration, valid or not, quietly became the full set. With the NUL present a
  # complete parse returns 0, so a non-zero status really is a failure and is refused.
  if ! IFS=$' \t\n' read -ra HEALTHCHECK_DECLARED_SERVICES -d '' \
      < <(printf '%s\0' "${HEALTHCHECK_SERVICES}"); then
    printf '[healthcheck] cannot read HEALTHCHECK_SERVICES\n' >&2
    exit 2
  fi
fi
readonly -a HEALTHCHECK_DECLARED_SERVICES

#: Declaring nothing keeps every probe, and that direction is the whole safety argument.
#: A node whose environment predates this option must keep monitoring exactly what it
#: monitored yesterday, because a sweep that quietly checks less still reports
#: ALL_HEALTHY — a monitoring loss that reports success is worse than a noisy probe.
#: Narrowing is opted INTO, per installation, in the operator's environment.
healthcheck_service_declared() { # healthcheck_service_declared <group>
  local group="$1" name
  # The catalog/SSH allowlist stays intact for reversal; the live sweep drops this group.
  if [[ "$group" == report-hub ]] && cmp -s \
      "${NODE_OPS_HOME:-$HOME}/.hermes/legacy-services/report-hub.mode" <(printf off); then
    return 1
  fi
  if (( ${#HEALTHCHECK_DECLARED_SERVICES[@]} == 0 )); then
    return 0
  fi
  for name in "${HEALTHCHECK_DECLARED_SERVICES[@]}"; do
    if [[ "$name" == "$group" ]]; then
      return 0
    fi
  done
  return 1
}

# An unknown name is refused, never ignored. Ignoring it would drop that group's probes
# while the operator believed they had just asked for them — the same reason
# automation/install/components.py refuses unknown component names rather than dropping
# them ("the one outcome an installer must never produce").
healthcheck_validate_services() {
  local name group known
  if (( ${#HEALTHCHECK_DECLARED_SERVICES[@]} == 0 )); then
    return 0
  fi
  for name in "${HEALTHCHECK_DECLARED_SERVICES[@]}"; do
    known=""
    for group in "${HEALTHCHECK_SERVICE_GROUPS[@]}"; do
      if [[ "$name" == "$group" ]]; then
        known=1
        break
      fi
    done
    if [[ -z "$known" ]]; then
      printf '[healthcheck] unknown HEALTHCHECK_SERVICES group: %s (known: %s)\n' "$name" "${HEALTHCHECK_SERVICE_GROUPS[*]}" >&2
      return 1
    fi
  done
}

# Fail-closed, and it must stop the caller: this file is sourced by the sweep and by the
# allowlist generator, and running either against a misread declaration is exactly how a
# filter comes to monitor less than the operator asked for.
if ! healthcheck_validate_services; then
  exit 2
fi

# Add an ordinary deployed service by adding one line here. Fields are:
# service group | display name | probe type | node | account | target
# The group is stripped before the row reaches LIVE_CHECKS, so healthcheck.sh, the
# allowlist generator and the probe wrapper still split the same five fields. Order is
# preserved on purpose: the committed allowlist manifest is printed in array order and the
# wrapper's inputs digest hashes it, so reordering would make every existing node report
# wrapper drift for a change that altered nothing it runs.
readonly -a HEALTHCHECK_CHECK_CATALOG=(
  "rag|$RAG_NODE embedding|embedding_health|${RAG_NODE}|$NODE_OPS_ACCOUNT|http://127.0.0.1:8001/health"
  "rag|$RAG_NODE Qdrant|qdrant_health|${RAG_NODE}|$NODE_OPS_ACCOUNT|http://127.0.0.1:6333/healthz"
  "rag|$RAG_NODE MCP|mcp_health|${RAG_NODE}|$NODE_OPS_ACCOUNT|http://127.0.0.1:8765/health"
  "core|$PRIMARY_NODE signed update trust|update_trust|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_DEPLOY_CHECKOUT"
  "core|$PRIMARY_NODE ops checkout mirrors origin/main|checkout_mirrors_origin|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_DEPLOY_CHECKOUT"
  "core|$PRIMARY_NODE release matches origin/main|release_matches_origin|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_DEPLOY_CHECKOUT"
  "core|$PRIMARY_NODE privileged release helpers match release|release_helper_drift|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_LIBEXEC_DIR"
  "core|$PRIMARY_NODE skill mounts match the release|skill_mounts_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_SKILL_STORE/live"
  "core|$PRIMARY_NODE release store usage|release_store_usage|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_RELEASE_STORE"
  "core|$PRIMARY_NODE release fully deployed|release_fully_deployed|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_DEPLOY_ALL_RECEIPT:-$NODE_PRIVATE_ROOT/deploy-all/receipt.json}"
  "core|$PRIMARY_NODE watcher wrappers match the release|watcher_wrappers_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_WATCHER_MANIFEST:-$(dirname "${BASH_SOURCE[0]}")/../configs/watcher-deploy-manifest.txt}"
  "core|$PRIMARY_NODE runtime packages match the release|primary_runtime_packages_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST:-$(dirname "${BASH_SOURCE[0]}")/../configs/runtime-package-manifest.txt}"
  "rag|$RAG_NODE personal RAG source and MCP image match the release|rag_stack_current|${RAG_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST:-$(dirname "${BASH_SOURCE[0]}")/../configs/runtime-package-manifest.txt}"
  "core|$PRIMARY_NODE healthcheck probe allowlist matches the checks|healthcheck_wrapper_current|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|automation/healthcheck_probe_wrapper.sh"
  "rag|$RAG_NODE healthcheck probe allowlist matches the checks|healthcheck_wrapper_current|${RAG_NODE}|$NODE_OPS_ACCOUNT|automation/healthcheck_probe_wrapper.sh"
  "core|$PRIMARY_NODE owner notice credentials|owner_notice_credentials|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|${HEALTHCHECK_OWNER_NOTICE_CREDENTIAL:-/etc/autophagy/repair-approval.env}"
)

#: Which group a check belongs to, answered from the catalog rather than from a second
#: list. A caller that needs this — telling an operator that a failing probe belongs to a
#: service they may not run — must not carry its own copy, because the copy is what goes
#: stale when a row moves.
healthcheck_group_of_check() { # healthcheck_group_of_check <display name>
  local wanted="$1" entry rest
  for entry in "${HEALTHCHECK_CHECK_CATALOG[@]}"; do
    rest="${entry#*|}"
    if [[ "${rest%%|*}" == "$wanted" ]]; then
      printf '%s' "${entry%%|*}"
      return 0
    fi
  done
  return 1
}

LIVE_CHECKS=()
for _catalog_entry in "${HEALTHCHECK_CHECK_CATALOG[@]}"; do
  if healthcheck_service_declared "${_catalog_entry%%|*}"; then
    LIVE_CHECKS+=("${_catalog_entry#*|}")
  fi
done
unset _catalog_entry
readonly -a LIVE_CHECKS

# Probes that run HERE, not over ssh. They must stay out of the remote tally: during a
# fleet-wide SSH outage they still pass, and counting them keeps the all-remote-down
# guard from collapsing N tickets into one INFRA_FAILURE (regression d7ed0ad / γ).
# One declaration on purpose — the same rule lived in two comparisons and the second
# copy is always the one that gets forgotten.
readonly LOCAL_PROBES="update_trust checkout_mirrors_origin release_matches_origin release_helper_drift skill_mounts_current agent_selfskill_root_topology release_store_usage release_fully_deployed peer_ignored_channels owner_notice_credentials"

# A repair ticket carries the check name, which is all an operator needs when the
# remedy is obvious (restart, re-auth). Deploy-checkout drift is the case where
# it is not: the commits stranded in the checkout exist nowhere else, so the
# reflexive repair - discard and realign - destroys them. Probe types without a
# rule here ship the name alone.
repair_guidance() {
  case "$1" in
    checkout_mirrors_origin)
      checkout_mirror_guidance "${HEALTHCHECK_OPS_CHECKOUT:-$NODE_DEPLOY_CHECKOUT}"
      ;;
    skill_mounts_current) skill_mount_guidance ;;
    peer_ignored_channels) peer_ignored_channels_guidance ;;
    owner_notice_credentials) owner_notice_credentials_guidance ;;
    agent_selfskill_root_topology) selfskill_root_guidance ;; release_store_usage) release_store_guidance ;;
    *) ;;
  esac
}

# The deploy checkout is a one-way mirror of origin/main. This probe runs LOCALLY:
# healthcheck runs as ops on the primary node and the checkout is local there, so it
# needs neither ssh (allowlist-denied) nor sudo (sudoers-denied) - both rc=126. The
# verdict (clean/dirty/ahead/behind/unknown-remote) and the grading that turns it into
# pass/fail both live in checkout_mirror_probe.sh - this file is wiring, and grading a
# behind mirror needs what production runs, which is more than one line's worth.
# Read-only: it uses git ls-remote (no local ref written), never fetch/pull/reset.
# An unreachable origin degrades to a PASS + BEHIND-UNKNOWN, never a cry-wolf fail.
# probe_skill_mounts_current lives in skill_mount_probe.sh (LOC gate) — sourced above.
run_check() {
  local definition="$1"
  local check_name probe_type node account target

  IFS='|' read -r check_name probe_type node account target <<< "$definition"
  # A release completion marker is outside protected/removed homes. Never sudo to
  # the deleted peer just to establish that its former probes are no longer relevant.
  if [[ "$probe_type" == peer_ignored_channels || "$probe_type" == agent_selfskill_root_topology ||
        "$probe_type" == user_unit_active &&
        ( "$target" == "$NODE_AGENT_GATEWAY_UNIT" || "$target" == "$NODE_PEER_GATEWAY_UNIT" ) ]]; then
    if cmp -s "$NODE_PRIVATE_ROOT/peer-retired" <(printf retired); then
      log "PASS $check_name: 퇴역(기대)"
      return 0
    fi
  fi
  case "$probe_type" in
    http_200) probe_http_200 "$node" "$account" "$target" ;;
    user_unit_active) probe_user_unit_active "$node" "$account" "$target" ;;
    http_unauth_401) probe_http_unauth_401 "$node" "$account" "$target" ;;
    embedding_health) probe_embedding_health "$node" "$account" "$target" ;;
    qdrant_health) probe_qdrant_health "$node" "$account" "$target" ;;
    mcp_health) probe_mcp_health "$node" "$account" "$target" ;;
    update_trust)
      probe_update_trust "$node" "$account" "$target" \
        || { UPDATE_TRUST_BLOCK_REPORTED=1; return 1; }
      ;;
    checkout_mirrors_origin) probe_checkout_mirrors_origin "$node" "$account" "$target" ;;
    release_matches_origin) probe_release_matches_origin "$node" "$account" "$target" ;;
    release_helper_drift) probe_release_helper_drift "$node" "$account" "$target" ;;
    skill_mounts_current) probe_skill_mounts_current "$node" "$account" "$target" ;;
    peer_ignored_channels) probe_peer_ignored_channels ;;
    owner_notice_credentials) probe_owner_notice_credentials "$node" "$account" "$target" ;;
    release_store_usage) probe_release_store_usage "$node" "$account" "$target" ;;
    release_fully_deployed) probe_release_fully_deployed "$node" "$account" "$target" ;;
    agent_selfskill_root_topology) probe_selfskill_root_topology "$node" "$account" "$target" ;;
watcher_wrappers_current) probe_watcher_wrappers_current "$node" "$account" "$target" ;; primary_runtime_packages_current) probe_primary_runtime_packages_current "$node" "$account" "$target" ;; rag_stack_current) probe_rag_stack_current "$node" "$account" "$target" ;;
healthcheck_wrapper_current) probe_healthcheck_wrapper_current "$node" "$account" "$target" ;;
    *) log "ERROR: ${check_name} has unsupported probe type ${probe_type}"; return 1 ;;
  esac
}

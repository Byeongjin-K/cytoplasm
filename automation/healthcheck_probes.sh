#!/usr/bin/env bash
# Remote probe implementations for healthcheck.sh. Sourced, never executed directly.

if [[ -z "${HEALTHCHECK_PROBES_LOADED:-}" ]]; then
  readonly HEALTHCHECK_PROBES_LOADED=1

  # Capture only a probe's response for validation. No response body is printed
  # or logged, so logs contain only check names and up/down state.
  capture_on_node() {
    local node="$1"
    local remote_command="$2"
    local result ssh_target="$node" command_status=0

    [[ -z "$SSH_REMOTE_USER" ]] || ssh_target="${SSH_REMOTE_USER}@${node}"
    result="$(timeout 45 ssh "${SSH_OPTIONS[@]}" "$ssh_target" "$remote_command" 2>&1)" || command_status=$?
    result="$(printf '%s\n' "$result" | grep -v 18789 || true)"

    (( command_status == 0 )) || return "$command_status"
    printf '%s' "$result"
  }

  probe_http_200() {
    local node="$1"
    local account="$2"
    local url="$3"
    local status

    valid_account "$account" && valid_http_url "$url" || return 1
    status="$(capture_on_node "$node" "sudo -n -u ${account} -H curl --fail --silent --output /dev/null --write-out '%{http_code}' '${url}'")" || return 1
    [[ "$status" == "200" ]]
  }

  probe_user_unit_active() {
    local node="$1"
    local account="$2"
    local unit="$3"
    local status

    valid_account "$account" && valid_unit "$unit" || return 1
    if [[ "$unit" == "${NODE_AGENT_GATEWAY_UNIT:-hermes-gateway.service}" && "$account" == "${NODE_AGENT_ACCOUNT:-agent}" ||
          "$unit" == "${NODE_PEER_GATEWAY_UNIT:-hermes-gateway.service}" && "$account" == "${NODE_PEER_ACCOUNT:-peer}" ]]; then
      status="$(capture_on_node "$node" "sudo -n -u ${account} -H bash -c 'export XDG_RUNTIME_DIR=/run/user/\$(id -u); state=\$(systemctl --user is-active ${unit}); if [[ \"\$state\" == inactive || \"\$state\" == failed ]] && cmp -s \"\$HOME/.hermes/gateway-stopped-by-release\" <(printf stopped); then printf stopped-by-release; elif [[ \"\$state\" == active ]]; then printf active; else exit 1; fi'")" || return 1
      if [[ "$status" == stopped-by-release ]]; then
        log "PASS $account $unit: 릴리스로 정지됨(정상, stopped by release — expected)"
        return 0
      fi
    else
      status="$(capture_on_node "$node" "sudo -n -u ${account} -H XDG_RUNTIME_DIR=/run/user/\$(id -u ${account}) systemctl --user is-active ${unit}")" || return 1
    fi
    [[ "$status" == "active" ]]
  }

  # The W3-4 dashboard must both respond and refuse unauthenticated access, so
  # the healthy result for a credential-free probe is exactly HTTP 401.
  probe_http_unauth_401() {
    local node="$1"
    local account="$2"
    local url="$3"
    local status

    valid_account "$account" && valid_http_url "$url" || return 1
    status="$(capture_on_node "$node" "sudo -n -u ${account} -H curl --silent --output /dev/null --write-out '%{http_code}' '${url}'")" || return 1
    [[ "$status" == "401" ]]
  }

  # The following three probes use exactly the W2-1 integration commands and
  # validate their documented healthy results without printing response bodies.
  probe_embedding_health() {
    local node="$1"
    local account="$2"
    local url="$3"
    local response

    valid_account "$account" && valid_http_url "$url" || return 1
    # One slow answer is not an outage (2026-10-18: a single 26 s reply under load opened an incident).
    # Each try is capped; a second try runs only after the first failed, and two failures stay a failure.
    local attempt
    for attempt in 1 2; do
      response="$(capture_on_node "$node" "sudo -n -u ${account} -H curl --fail --silent --max-time 10 '${url}'")" || continue
      [[ "$response" == *'"status"'*'"ok"'* && "$response" == *'"model"'*'"BAAI/bge-m3"'* && "$response" == *'"dimensions"'*1024* ]] && return 0
    done
    return 1
  }

  probe_qdrant_health() {
    local node="$1"
    local account="$2"
    local url="$3"
    local response

    valid_account "$account" && valid_http_url "$url" || return 1
    response="$(capture_on_node "$node" "sudo -n -u ${account} -H curl --fail --silent '${url}'")" || return 1
    [[ "$response" == "healthz check passed" ]]
  }

  probe_mcp_health() {
    local node="$1"
    local account="$2"
    local url="$3"
    local response

    valid_account "$account" && valid_http_url "$url" || return 1
    response="$(capture_on_node "$node" "sudo -n -u ${account} -H curl --fail --silent '${url}'")" || return 1
    [[ "$response" == *'"status"'*'"ok"'* && "$response" == *'"collection"'*'"personal_cha"'* ]]
  }
fi

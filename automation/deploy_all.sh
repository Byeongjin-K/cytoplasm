#!/usr/bin/env bash
# automation/deploy_all.sh — origin/main 전량 수렴 오케스트레이터 (RC-3/4).
#
# 지금까지 "배포했다"의 단위는 표면별·스킬별·패키지별이라, 스킬 6개 중 2개만 배포된
# 상태와 전부 배포된 상태가 명령 수준에서 구분되지 않았다(C3). 이 명령은 그 질문을
# 하나로 접는다 — 판정은 노드 릴리스 트리의 `deploy_all_probe.py` 가 내고(관측과 판정이
# 같은 세대의 코드), 실행은 기존 배포기(`deploy-skill.sh`·`<pkg>/deploy.sh`)를 부를
# 뿐이며(배포 로직 사본 0), 실행 성공과 마지막 **전량 재판정**을 모두 통과해야 영수증을 쓴다 —
# 부분 성공은 성공으로 보고되지 않는다.
#
# Usage: deploy_all.sh [--plan|--verify|--rehearse|--apply [--wait-converge]]
#   --rehearse 카드 전 읽기 전용 리허설: 이 체크아웃 HEAD 의 트리를 노드 임시 폴더에 놓고 전량 적용과
#            같은 계정·sudo 모양으로 판정과 승인된 제거 단계의 검사(--check)만 돌린다. 인가·배포기·
#            재시동·쓰기 없음. 마지막 줄 REHEARSAL-OK|REHEARSAL-REFUSED (rc 0|4)
#   --plan   (기본) 판정 출력만. rc 0=전량 일치 · 1=할 일 있음 · 4=판정 불가
#   --verify 판정 + 전량 일치면 영수증 기록(수렴 검증만, 배포 없음)
#   --apply  선택적 --wait-converge 시 노드 릴리스 트리 수렴을 먼저 기다린 뒤
#            계획된 배포기 실행(스킬은 기존 승인 게이트 그대로) → 플러그인 갱신 시
#            게이트웨이 재시동(「게이트웨이 재시동 규칙」— 항상 agent+peer 전 세트, 배포기가
#            하나라도 실패하면 재시동하지 않는다) → 게이트웨이가 현재 릴리스 세대를 등록할
#            때까지 대기(DEPLOY_ALL_GATEWAY_WAIT_SECONDS·_INTERVAL) → 전량 재판정 → 영수증
#
# ⑤ root 자산·⑥ RAG 는 상시 healthcheck 프로브가 소유한다(영수증의 delegated 필드) —
# root 자산은 여기서 실행하지 않는다. RAG MCP는 카드에 묶인 릴리스 승인 뒤에만 갱신한다.
#
# Exit: 0 ok(영수증 기록) · 1 실행 실패/drift/재판정 실패 · 2 usage · 3 host 미설정 · 4 판정 불가/전제 미충족
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:---plan}"
wait_converge=0
if [[ "$mode" == "--help" || "$mode" == "-h" ]]; then
  if (( $# > 1 )); then
    echo "usage: deploy_all.sh [--plan|--verify|--rehearse|--apply [--wait-converge]]" >&2
    exit 2
  fi
  echo "usage: deploy_all.sh [--plan|--verify|--rehearse|--apply [--wait-converge]]"
  exit 0
fi
if (( $# > 1 )); then
  if [[ "$mode" == "--apply" && $# == 2 && "$2" == "--wait-converge" ]]; then
    wait_converge=1
  else
    echo "usage: deploy_all.sh [--plan|--verify|--rehearse|--apply [--wait-converge]]" >&2
    exit 2
  fi
fi
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [[ -z "$host" ]]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)." >&2
  exit 3
fi
# 도리 전환은 릴리스 호스트의 파일이다. 판정 프로브는 노드에서 돌므로 여기서 읽어 매 호출에 넘긴다 —
# 노드 홈에는 그 파일이 없어서, 프로브가 스스로 읽으면 늘 off 로 보고 승인된 켬을 「일치」로 건너뛴다(v1.50.0).
if ! dori_bot_id="$(PYTHONPATH="$repo_root" python3 -B -m automation.hermes_agent_chat_ignore --release-switch)"; then
  echo "DORI-SWITCH-REFUSED: invalid release-host switch ~/.hermes/release-flags/dori-agent-chat" >&2
  exit 4
fi
dori_mode=off
[[ -z "$dori_bot_id" ]] || dori_mode=on
peer_mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.peer_retired --release-switch)" || exit 4
product_mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.hermes_product --release-switch)" || exit 4
retirement_mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.gateway_retirement --release-switch)" || exit 4
if ! gateway_mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.hermes_gateways --release-switch)"; then
  echo "GATEWAY-STOP-REFUSED: invalid release-host switch ~/.hermes/release-flags/hermes-gateways-stopped" >&2
  exit 4
fi
chat_channel="$(PYTHONPATH="$repo_root" python3 -B -m automation.yoodori_channel --release-switch)" || exit 4
chat_channel="${chat_channel:-off}"
notice_digest="$(PYTHONPATH="$repo_root" python3 -B -m automation.notice_bot_release digest)" || exit 4
fault_channel="$(PYTHONPATH="$repo_root" python3 -B -m automation.notice_bot_release fault-channel)" || exit 4
if [[ "$fault_channel" != off && "$notice_digest" == off ]]; then
  echo "FAULT-CHANNEL-REFUSED: notice-bot required" >&2
  exit 4
fi
auto_kinds="$(PYTHONPATH="$repo_root" python3 -B -m automation.auto_write_release switches)" || exit 4
ledger_mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.ledger.release switch)" || exit 4
relay_mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.omo_relay_release switch)" || exit 4
report_mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.legacy_services switch --name report-hub)" || exit 4
dashboard_mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.legacy_services switch --name hermes-dashboard)" || exit 4
memory_mode="$(PYTHONPATH="$repo_root" python3 -B -m automation.legacy_services switch --name hermes-memory)" || exit 4
if [[ "$peer_mode" == retired && "$gateway_mode" != stopped ]]; then
  echo "PEER-RETIRED-REFUSED: stopped gateways required before removal" >&2
  exit 4
fi
if [[ "$notice_digest" != off && ( "$gateway_mode" != stopped || "$chat_channel" == off ) ]]; then
  echo "NOTICE-BOT-REFUSED: stopped gateways and yoodori channel required" >&2
  exit 4
fi

readonly RUNTIME_ROOT="${DEPLOY_ALL_RUNTIME_ROOT:-/srv/autophagy-agent-current}"
readonly RECEIPT_DIR="${DEPLOY_ALL_RECEIPT_DIR:-/srv/autophagy-private/deploy-all}"
# --rehearse points these at the staged candidate tree and turns every removal into its check.
exec_root="$RUNTIME_ROOT"
check_flag=""
rehearse=0

log() { printf '[deploy-all] %s\n' "$*" >&2; }

probe() { # probe <format> [extra args...] — 노드 릴리스 트리의 판정을 그대로 중계한다
  local format="$1" extra=""
  shift
  (( $# )) && printf -v extra ' %q' "$@"
  ssh "$host" "python3 $exec_root/automation/deploy_all_probe.py --ledger $ledger_mode --omo-relay $relay_mode --report-hub $report_mode --hermes-dashboard $dashboard_mode --hermes-memory $memory_mode --peer-retired $peer_mode --auto-write $auto_kinds --notice-bot $notice_digest --fault-channel $fault_channel --dori-agent-chat $dori_mode --hermes-gateways $gateway_mode --yoodori-channel $chat_channel --format $format$extra" < /dev/null
}

converge_ledger() {
  PYTHONPATH="$repo_root" python3 -B -m automation.ledger.release apply \
    --repo "$repo_root" --host "$host" --runtime "$RUNTIME_ROOT" --head "$node_sha" --mode "$ledger_mode"
}

retire_peer() {
  local acct role
  (( rehearse )) || "$repo_root/automation/release_approval_remote.sh" authorize-peer-retired --head "$node_sha" \
    || { log "PEER-RETIRED-UNAUTHORIZED: approved HEAD does not cover P5"; return 4; }
  # The private marker is deliberately written only after both account cleanups.
  # It does not delete the peer account or grant any new privilege.
  for role in agent peer; do
    acct="$NODE_AGENT_ACCOUNT"
    [[ "$role" != peer ]] || acct="$NODE_PEER_ACCOUNT"
    # The marker is ops 0600 under the 0700 private root, so only ops can read it (2026-10-19:
    # read as the ssh login it always failed and v2.4.0 stopped at `sudo: unknown user peer`).
    if [[ "$role" == peer ]] && ssh "$host" "sudo -n -u $NODE_OPS_ACCOUNT -H bash -c 'cmp -s $NODE_PRIVATE_ROOT/peer-retired <(printf retired)' && ! getent passwd $acct >/dev/null" </dev/null; then
      continue  # Owner root block removed the account after a completed non-root cleanup.
    fi
    ssh "$host" "sudo -n -u $acct -H env PYTHONPATH=$exec_root python3 -B -m automation.p5_remove --role $role --head $node_sha$check_flag" </dev/null \
      || return 4
  done
  (( rehearse )) && return 0
  ssh "$host" "sudo -n -u $NODE_OPS_ACCOUNT -H bash -c 'umask 077; printf retired > $NODE_PRIVATE_ROOT/peer-retired; cmp -s $NODE_PRIVATE_ROOT/peer-retired <(printf retired)'" </dev/null
}

retire_hermes_product() {
  (( rehearse )) || "$repo_root/automation/release_approval_remote.sh" authorize-hermes-product --head "$node_sha" \
    || { log "HERMES-PRODUCT-UNAUTHORIZED: 승인 HEAD에 제품 제거 표면 없음"; return 4; }
  ssh "$host" "sudo -n -u $NODE_AGENT_ACCOUNT -H env PYTHONPATH=$exec_root python3 -B -m automation.hermes_product --remove --head $node_sha$check_flag" </dev/null
}

retire_spend_gateway() {
  (( rehearse )) || "$repo_root/automation/release_approval_remote.sh" authorize-gateway-retirement --head "$node_sha" \
    || { log "GATEWAY-RETIRE-UNAUTHORIZED: 승인 HEAD에 제거 표면 없음"; return 4; }
  local acct role
  for role in ops agent; do
    acct="$NODE_OPS_ACCOUNT"
    [[ "$role" != agent ]] || acct="$NODE_AGENT_ACCOUNT"
    ssh "$host" "sudo -n -u $acct -H env PYTHONPATH=$exec_root python3 -B -m automation.gateway_retirement --remove --role $role --head $node_sha$check_flag" </dev/null \
      || return 4
  done
}

converge_auto_write() {
  PYTHONPATH="$repo_root" python3 -B -m automation.auto_write_release apply \
    --repo "$repo_root" --host "$host" --runtime "$RUNTIME_ROOT" --head "$node_sha" \
    --kinds "$auto_kinds"
}

converge_notice_bot() {
  local -a retired_args=()
  [[ "$peer_mode" != retired ]] || retired_args=(--peer-retired)
  PYTHONPATH="$repo_root" python3 -B -m automation.notice_bot_release apply \
    --repo "$repo_root" --host "$host" --runtime "$RUNTIME_ROOT" --head "$node_sha" \
    --digest "$notice_digest" --channel "$chat_channel" --fault-channel "$fault_channel" "${retired_args[@]}"
}

#: 헬스체크 허용 목록 래퍼 — 릴리스 카드가 갱신을 실었고 소유자가 ✅ 한 노드만 설치한다
#: (healthcheck_wrapper_release.sh refresh). 결과는 영수증에 실려 적용 통지로 소유자에게 간다 —
#: 실패해도 영수증을 막지 않는다(위임 표면은 영수증을 막지 않는 것과 같다).
wrapper_receipt_args=()
rag_receipt_args=()
refresh_rag_mcp() {
  local -a refresh_cmd
  local out rc=0 line result=""
  read -r -a refresh_cmd <<< "${DEPLOY_ALL_RAG_MCP_REFRESH_CMD:-bash $repo_root/automation/rag_mcp_release.sh refresh}"
  out="$("${refresh_cmd[@]}" "$local_head")" || rc=$?
  while IFS= read -r line; do
    [[ "$line" == RAG-MCP-REFRESH\|* ]] || continue
    log "$line"
    result="${line#RAG-MCP-REFRESH|}"
  done <<< "$out"
  # A cut transport or a crash before the sentinel cannot be reported as success.
  [[ -n "$result" || "$rc" == 0 ]] || result="failed|-|-|refresh-unfinished"
  rag_receipt_args=()
  [[ -z "$result" ]] || rag_receipt_args=(--rag-mcp-refresh "$result")
}
refresh_wrappers() {
  local -a refresh_cmd
  local out line _tag node status token
  read -r -a refresh_cmd <<< "${DEPLOY_ALL_WRAPPER_REFRESH_CMD:-bash $repo_root/automation/healthcheck_wrapper_release.sh refresh}"
  out="$("${refresh_cmd[@]}" "$local_head")" || log "WRAPPER-REFRESH-UNKNOWN: the refresh step did not finish"
  wrapper_receipt_args=()
  while IFS= read -r line; do
    [[ "$line" == WRAPPER-REFRESH\|* ]] || continue
    log "$line"
    IFS='|' read -r _tag node status token <<< "$line"
    [[ "$status" == current ]] || wrapper_receipt_args+=(--wrapper-refresh "$node|$status|$token")
  done <<< "$out"
}

node_release_sha() {
  local target
  target="$(ssh "$host" "readlink $RUNTIME_ROOT" < /dev/null)" || return 1
  basename "${target//[[:space:]]/}"
}

#: 노드가 **왜** 수렴을 거부했는지는 리컨실러 저널에만 있다 — 상태 파일은 실패 횟수만
#: 세고 사유 문구를 남기지 않는다. 그래서 2026-09-22 에는 여기서 "waiting…" 를 40번 찍고
#: rc=4 로 죽은 뒤, 진짜 사유(`SNAPSHOT-BLOCK: remote main moved`)를 사람이 노드 저널을
#: 뒤져서야 알았다. 진단 전용이라 실패해도 종료 코드를 바꾸지 않는다.
converge_refusal() {
  ssh "$host" "journalctl -u autophagy-deploy-reconcile.service -n 300 --no-pager \
    | grep -E 'HELPER-FAILED|SNAPSHOT-BLOCK|UPDATE-TRUST-BLOCK|SYNC-BLOCK|RELEASE-ROLLBACK' \
    | tail -n 1" < /dev/null 2>/dev/null
}

restart_gateways() { # 「게이트웨이 재시동 규칙」(2026-07-22) — 한쪽만 재시동 금지
  local acct
  for acct in agent peer; do
    log "restarting $acct gateway"
    ssh "$host" "sudo -n -u $acct -H bash -lc 'export XDG_RUNTIME_DIR=/run/user/\$(id -u); systemctl --user restart hermes-gateway.service && systemctl --user is-active hermes-gateway.service'" < /dev/null \
      || { log "GATEWAY-RESTART-FAIL: $acct"; return 1; }
  done
}

change_gateways() { # Reversible release effect: attempt both accounts, including after a failure.
  local verb="$1" acct script failed=0
  for acct in agent peer; do
    if [[ "$verb" == stop ]]; then
      script='export XDG_RUNTIME_DIR=/run/user/$(id -u); umask 077; mkdir -p "$HOME/.hermes" && printf stopped > "$HOME/.hermes/gateway-stopped-by-release" && systemctl --user stop hermes-gateway.service && systemctl --user reset-failed hermes-gateway.service && [[ "$(systemctl --user is-active hermes-gateway.service)" == inactive ]]'
    else
      script='export XDG_RUNTIME_DIR=/run/user/$(id -u); rm -f -- "$HOME/.hermes/gateway-stopped-by-release" && systemctl --user start hermes-gateway.service && systemctl --user is-active hermes-gateway.service'
    fi
    log "$verb $acct gateway"
    ssh "$host" "sudo -n -u $acct -H bash -lc '$script'" </dev/null \
      || { log "GATEWAY-${verb^^}-FAIL: $acct"; failed=1; }
  done
  return "$failed"
}

#: `systemctl restart` 는 exec 직후 돌아온다 — 게이트웨이가 플러그인을 등록해 세대를 기록하기 전이다.
#: 그래서 계획 시점의 줄이 아니라 재시동 뒤의 노드 관측으로 stale·unknown 줄이 없어질 때까지 기다린다
#: (게이트웨이 묶음이 처음 놓이는 실행에서는 계획 시점에 드롭인 행이 stale 이라 줄 자체가 없었다).
wait_gateway_generation() {
  local limit="${DEPLOY_ALL_GATEWAY_WAIT_SECONDS:-120}" interval="${DEPLOY_ALL_GATEWAY_WAIT_INTERVAL:-3}" out deadline
  [[ "$limit" =~ ^[0-9]+$ ]] || limit=120
  [[ "$interval" =~ ^[0-9]+$ ]] || interval=3
  deadline=$(( SECONDS + limit ))
  while :; do
    out="$(probe gateways)" && ! grep -qE '^OBS[|]gateway[|][^|]*[|](stale|unknown)[|]' <<< "$out" && return 0
    if (( SECONDS >= deadline )); then
      log "GATEWAY-GENERATION-TIMEOUT: the gateways did not register the release plugins within ${limit}s"
      grep -E '^OBS[|]gateway[|]' <<< "$out" | head -n 8 >&2
      return 1
    fi
    sleep "$interval"
  done
}

write_receipt() {
  # 영수증 내용은 노드가 **지금 다시 판정**해 만든다 — 아까의 판정을 재사용하면
  # 그 사이의 변화가 서명된다. read-back 대조는 deploy_push 와 같은 규율이다.
  local receipt want got
  if ! receipt="$(probe receipt "${wrapper_receipt_args[@]}" "${rag_receipt_args[@]}")"; then
    log "RECEIPT-BLOCK: the node no longer judges itself clean"
    return 1
  fi
  want="$(printf '%s' "$receipt" | sha256sum | cut -d' ' -f1)"
  printf '%s' "$receipt" | ssh "$host" "sudo -n -u ops -H bash -c 'umask 027; mkdir -p $RECEIPT_DIR && cat > $RECEIPT_DIR/receipt.json'" \
    || { log "RECEIPT-BLOCK: could not write the receipt"; return 1; }
  got="$(ssh "$host" "sudo -n -u ops -H bash -c 'sha256sum $RECEIPT_DIR/receipt.json | cut -d\" \" -f1'" < /dev/null)"
  got="${got//[[:space:]]/}"
  if [[ "$got" != "$want" ]]; then
    log "RECEIPT-BLOCK: read-back mismatch (want=${want:0:16} got=${got:0:16})"
    return 1
  fi
  log "receipt written: $RECEIPT_DIR/receipt.json"
}

case "$mode" in
  --plan)
    probe report
    exit $?
    ;;
  --verify)
    if probe report; then
      write_receipt
      exit $?
    fi
    rc=$?
    (( rc == 1 )) && log "not fully deployed — no receipt (run --apply, or deploy the listed items)"
    exit "$rc"
    ;;
  --rehearse)
    # 2026-10-19 리드 규칙(형이 ✅ 를 세 번 다시 눌렀다): 카드 전에 전량 적용의 노드 쪽 검사를
    # 끝까지 통과시킨다. 노드는 아직 이전 릴리스이므로 후보 트리를 임시 폴더에 실어 그 코드로 돈다.
    node_sha="$(git -C "$repo_root" rev-parse HEAD)" || exit 4
    # release.sh reads the last result per HEAD before it posts a card; a refusal replaces an older pass.
    rehearsal_dir="${DEPLOY_ALL_REHEARSAL_DIR:-$HOME/.hermes/deploy-all/rehearsal}"
    rehearsal_end() { # rehearsal_end <rc> <last line>
      local record=""
      echo "$2"
      if mkdir -p -m 700 "$rehearsal_dir" && record="$(mktemp "$rehearsal_dir/.record.XXXXXX")" \
        && printf '%s %s\n%s\n' "$(date +%s)" "$node_sha" "$2" > "$record" \
        && mv -f "$record" "$rehearsal_dir/$node_sha"; then
        exit "$1"
      fi
      [[ -z "$record" ]] || rm -f "$record"
      log "REHEARSAL-RECORD-FAIL: $rehearsal_dir 에 결과를 남기지 못했다 — release.sh 가 카드를 거부한다"
      exit 4
    }
    stage="/tmp/deploy-rehearse.${node_sha:0:12}.$$"
    trap 'ssh "$host" "rm -rf $stage" </dev/null >/dev/null 2>&1 || true' EXIT
    if ! git -C "$repo_root" archive "$node_sha" | ssh "$host" "umask 022; mkdir $stage && tar -xf - -C $stage"; then
      rehearsal_end 4 "REHEARSAL-REFUSED step=stage rc=4"
    fi
    exec_root="$stage"
    check_flag=" --check"
    rehearse=1
    actions="$(probe actions)"
    rc=$?
    if (( rc == 4 )); then
      rehearsal_end 4 "REHEARSAL-REFUSED step=plan rc=4"
    fi
    grep -E '^ACT[|]' <<< "$actions" || true
    steps="plan"
    # The pinned Dori fork: fetch that exact commit and dry-run its installer (lead 2026-10-19).
    read -r -a pin_check <<< "${DEPLOY_ALL_DORI_PIN_CHECK_CMD:-python3 -B -m automation.dori_pin rehearse}"
    (cd "$repo_root" && PYTHONPATH="$repo_root" "${pin_check[@]}") \
      || rehearsal_end 4 "REHEARSAL-REFUSED step=dori-pin rc=4"
    steps+=",dori-pin"
    if [[ "$peer_mode" == retired ]]; then
      retire_peer || rehearsal_end 4 "REHEARSAL-REFUSED step=peer-retired rc=4"
      steps+=",peer-retired"
    fi
    if [[ "$product_mode" == retired ]]; then
      retire_hermes_product || rehearsal_end 4 "REHEARSAL-REFUSED step=hermes-product rc=4"
      steps+=",hermes-product"
    fi
    if [[ "$retirement_mode" == retired ]]; then
      retire_spend_gateway || rehearsal_end 4 "REHEARSAL-REFUSED step=spend-gateway rc=4"
      steps+=",spend-gateway"
    fi
    log "리허설 범위 밖: 카드 인가·배포기 실행·재시동·영수증(✅ 뒤 --apply 가 한다)"
    rehearsal_end 0 "REHEARSAL-OK head=${node_sha:0:12} actions=$(grep -c '^ACT[|]' <<< "$actions") steps=$steps"
    ;;
  --apply)
    # 배포기는 이 체크아웃의 파일을 밀므로, 체크아웃과 노드 릴리스가 같은 세대가
    # 아니면 "릴리스로 수렴"이 아니라 "체크아웃으로 오염"이 된다 — provenance 가드와
    # 같은 결의 fail-closed 다.
    local_head="$(git -C "$repo_root" rev-parse HEAD)" || exit 4
    apply_lock_dir="${DEPLOY_ALL_LOCK_DIR:-$HOME/.hermes/deploy-all}"
    if ! mkdir -p -m 700 "$apply_lock_dir" || ! chmod 700 "$apply_lock_dir"; then
      log "APPLY-LOCK-BLOCK: cannot secure $apply_lock_dir"
      exit 4
    fi
    if ! exec 8>"$apply_lock_dir/apply.lock"; then
      log "APPLY-LOCK-BLOCK: cannot open $apply_lock_dir/apply.lock"
      exit 4
    fi
    apply_lock_waited=0
    if ! flock -n 8; then
      apply_lock_waited=1
      log "APPLY-LOCK-WAIT: another deploy_all --apply is running — waiting"
      if ! flock -w "${DEPLOY_ALL_LOCK_WAIT_SECONDS:-1800}" 8; then
        log "APPLY-LOCK-TIMEOUT: could not acquire $apply_lock_dir/apply.lock"
        exit 1
      fi
    fi
    wait_timed_out=0
    if (( wait_converge )); then
      converge_deadline=$(( SECONDS + ${DEPLOY_ALL_CONVERGE_SECONDS:-600} ))
      while :; do
        node_sha="$(node_release_sha)" || node_sha="unreadable"
        [[ "$local_head" == "$node_sha" ]] && break
        log "waiting for the node release (have $node_sha want $local_head)"
        if (( SECONDS >= converge_deadline )); then
          wait_timed_out=1
          break
        fi
        sleep "${DEPLOY_ALL_CONVERGE_POLL_SECONDS:-10}"
      done
    else
      node_sha="$(node_release_sha)" || { log "RELEASE-UNKNOWN: cannot read the node release pointer"; exit 4; }
    fi
    if [[ "$local_head" != "$node_sha" ]]; then
      (( wait_timed_out )) && log "node release convergence wait timed out"
      log "RELEASE-MISMATCH: local HEAD ${local_head:0:12} != node release ${node_sha:0:12}"
      refusal="$(converge_refusal)"
      if [[ -n "$refusal" ]]; then
        log "NODE-CONVERGE-REFUSED: ${refusal:0:300}"
      else
        log "NODE-CONVERGE-REFUSED: 노드 저널에서 거부 사유를 찾지 못했다(아직 거부 전이거나 저널 회전·권한)"
      fi
      log "  릴리스 수렴(2분 리컨실러)을 기다리거나 체크아웃을 그 sha 로 맞춘 뒤 다시 실행"
      exit 4
    fi
    if [[ "$gateway_mode" == stopped ]]; then
      if ! "$repo_root/automation/release_approval_remote.sh" authorize-hermes-gateways-stopped --head "$node_sha"; then
        log "GATEWAY-STOP-UNAUTHORIZED: the deployed HEAD's approved card did not authorize stopping agent+peer"
        exit 4
      fi
    fi
    if [[ "$peer_mode" == retired ]]; then
      # No non-root cleanup can run until the deployed release card authorizes it.
      # Execute the node's signed runtime, never copy workstation code for removal.
      retire_peer || exit 4
    fi
    if [[ "$auto_kinds" != off ]]; then
      converge_auto_write || exit 4
    fi
    if [[ "$notice_digest" != off ]]; then
      "$repo_root/automation/release_approval_remote.sh" authorize-yoodori-channel \
        --channel-id "$chat_channel" --head "$node_sha" || exit 4
      converge_notice_bot || exit 4
    fi
    actions="$(probe actions)"
    rc=$?
    if (( rc == 4 )); then
      log "UNVERIFIABLE: the node cannot judge itself"
      exit 4
    fi
    # ACT|owner| 는 릴리스가 올릴 수 없는 소유자 조치다(root 자산·RAG) — 보여 주기만 하고
    # 실행하지도, 실패로 세지도 않는다. clean 이어도(rc 0) 나오므로 분기 전에 출력한다.
    grep -E '^ACT[|]owner[|]' <<< "$actions" || true
    if (( rc == 0 )); then
      if (( apply_lock_waited )); then
        log "APPLY-LOCK-RECHECK: already fully deployed after waiting for the apply lock"
      else
        log "already fully deployed"
      fi
      # The account's routing transaction is already checked by the probe.
      # A release with nothing else to deploy still owes its approved removals (v2.6.1 took this
      # branch and skipped the product removal). Both are idempotent behind completion markers.
      if [[ "$product_mode" == retired ]]; then
        retire_hermes_product || exit 4
      fi
      if [[ "$retirement_mode" == retired ]]; then
        retire_spend_gateway || exit 4
      fi
      refresh_wrappers
      refresh_rag_mcp
      write_receipt
      exit $?
    fi
    restart_needed=0
    gateway_change=""
    failures=()
    ledger_change=0
    # actions 는 fd 9 로 읽는다 — 배포기 내부의 ssh 가 fd 0 을 삼키므로, stdin 에
    # 실으면 첫 action 뒤의 줄이 전부 사라진다(2026-08-31 실측: 매 실행 1건만 배포).
    while IFS='|' read -r -u 9 tag kind arg; do
      [[ "$tag" == "ACT" ]] || continue
      case "$kind" in
        deploy-skill)
          log "deploying skill: $arg"
          "$repo_root/automation/deploy-skill.sh" "$arg" --release-approval \
            || failures+=("skill:$arg")
          ;;
        run-deployer)
          log "running deployer: $arg"
          "$repo_root/$arg" || failures+=("$arg")
          ;;
        notice-bot)
          converge_notice_bot || failures+=("notice-bot")
          ;;
        auto-write)
          converge_auto_write || failures+=("auto-write")
          ;;
        ledger)
          # Migrate only after every skill mount has the shared routing lock.
          ledger_change=1
          ;;
        restart-gateway)
          restart_needed=1
          ;;
        stop-gateway)
          [[ "$gateway_mode" != stopped ]] || gateway_change=stop
          ;;
        start-gateway)
          [[ "$gateway_mode" != running ]] || gateway_change=start
          ;;
        owner)
          ;;
        manual)
          log "MANUAL: $arg — 자동 수렴 대상이 아님"
          failures+=("manual:$arg")
          ;;
      esac
    done 9<<< "$actions"
    if [[ "$gateway_mode" == stopped ]]; then
      restart_needed=0
      # A bundle deployer may have started a gateway itself; reassert the approved
      # stop after every apply, even when its initial service observation was clean.
      gateway_change=stop
    fi
    if [[ "$peer_mode" == retired ]]; then
      restart_needed=0
      gateway_change=""
    fi
    if [[ -n "$gateway_change" ]] && { [[ "$gateway_change" == stop ]] || ((${#failures[@]} == 0)); }; then
      if ! change_gateways "$gateway_change"; then
        failures+=("gateway-$gateway_change")
      elif [[ "$gateway_change" == start && "$restart_needed" == 0 ]] && ! wait_gateway_generation; then
        failures+=("gateway-generation")
      fi
    fi
    if (( restart_needed )) && ((${#failures[@]})); then
      skip_cause="deployer failures"
      printf '%s\n' "${failures[@]}" | grep -qv '^manual:' || skip_cause="unresolved manual items"
      log "RESTART-SKIPPED: $skip_cause — a partial deploy is not activated"
    elif (( restart_needed )); then
      if ! restart_gateways; then
        failures+=("gateway-restart")
      elif ! wait_gateway_generation; then
        failures+=("gateway-generation")
      fi
    fi
    if ((${#failures[@]})); then
      log "incomplete: ${failures[*]}"
      exit 1
    fi
    if (( ledger_change )) || [[ "$ledger_mode" == on ]]; then
      converge_ledger || exit 4
    fi
    # Recheck product paths a deployer may have recreated, after ledger migration. The Hermes
    # product removal runs only here, after every deployer (2026-10-19: v2.3.0/v2.4.0 stopped at
    # an early retirement step with nothing else applied).
    if [[ "$peer_mode" == retired ]]; then
      retire_peer || exit 4
    fi
    if [[ "$product_mode" == retired ]]; then
      retire_hermes_product || exit 4
    fi
    if [[ "$retirement_mode" == retired ]]; then
      retire_spend_gateway || exit 4
    fi
    # 실행 성공 뒤에도 전량 재판정은 필수다. digest 일치가 실행 실패를 덮지는 않는다.
    if probe report; then
      refresh_wrappers
      refresh_rag_mcp
      write_receipt
      exit $?
    fi
    log "재판정 실패 — 영수증 없음"
    exit 1
    ;;
  *)
    echo "usage: deploy_all.sh [--plan|--verify|--rehearse|--apply [--wait-converge]]" >&2
    exit 2
    ;;
esac

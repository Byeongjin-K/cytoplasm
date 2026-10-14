#!/usr/bin/env bash
# automation/healthcheck_wrapper_release.sh — 헬스체크 허용 목록(강제명령 래퍼) 갱신을 릴리스 ✅ 하나에 묶는다.
#
# 소유자 지시(2026-10-14): "릴리스 승인 카드에 허용 목록 갱신을 같이 넣기(✅ 한 번으로 둘 다)".
# 래퍼는 릴리스 트리 밖(운영자 홈) 파일이라 수렴이 올리지 못했고, 릴리스마다 영수증의
# 「남은 소유자 조치」로 남아 사람이 노드에서 `--install` 을 돌려야 했다.
#
#   plan <head>     승인 **전**. <head> 트리를 주 노드의 전용 빈 임시 디렉터리에 파일로 보내 풀고
#                   (stdin 에 데이터를 싣지 않는다 — 명시한 명령만 실행), 노드마다 그 릴리스가 기대할
#                   `# wrapper-inputs:` 지문을 계산해 설치된 래퍼의 지문과 다르면 `<node>=<digest>` 한 줄.
#                   그 줄이 `release_approval plan --healthcheck-wrapper` 로 승인 해시에 묶이고 카드에
#                   「헬스체크 허용 목록도 갱신: …」 한 줄이 된다. rc 0 판정 완료(줄 없음=갱신 불필요) · 1 판정 불가
#   refresh <head>  수렴 **뒤**(`deploy_all.sh --apply`). 노드 릴리스 트리로 기대 지문을 다시 계산하고, 설치본과
#                   다르면 그 지문이 <head> 릴리스 승인에 묶였는지 묻는다(`release_approval authorize-wrapper`
#                   — 카드의 소유자 ✅ 만 인가한다). 인가되면 운영자 계정으로 설치하고 헤더 지문을 다시 읽어
#                   대조한다. 출력 `WRAPPER-REFRESH|<node>|<current|installed|skipped|failed>|<token>`, rc 0.
#
# sudo·authorized_keys 는 건드리지 않는다 — 설치기(provision-healthcheck-probe.sh)는 운영자 홈의 파일
# 하나만 바꾼다. 승인 조회만 기존 승인 경로(release_approval_remote.sh, agent 계정)를 그대로 쓴다.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
eval "$(python3 "$SCRIPT_DIR/node_config_sh.py" --print-env)" || exit 1

host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
runtime_root="${DEPLOY_ALL_RUNTIME_ROOT:-${NODE_RELEASE_CURRENT:-/srv/autophagy-agent-current}}"
local_node="${WRAPPER_RELEASE_LOCAL_NODE:-$(hostname -s)}"
read -r -a ssh_cmd <<< "${WRAPPER_RELEASE_SSH:-ssh -o BatchMode=yes}"
read -r -a scp_cmd <<< "${WRAPPER_RELEASE_SCP:-scp -q -o BatchMode=yes}"
read -r -a authorize_cmd <<< "${WRAPPER_RELEASE_AUTHORIZE_CMD:-$SCRIPT_DIR/release_approval_remote.sh}"
provisioner="${WRAPPER_RELEASE_PROVISIONER:-$SCRIPT_DIR/provision-healthcheck-probe.sh}"
readonly WRAPPER_FILE='$HOME/.local/libexec/autophagy-healthcheck-probe'

log() { printf '[wrapper-release] %s\n' "$*" >&2; }

nodes() {
  printf '%s\n' "${NODE_PRIMARY_NODE_NAME:-}" "${NODE_RAG_NODE_NAME:-}" | awk 'NF && !seen[$0]++'
}

#: 노드의 운영자 셸에서 명령 하나. 이 PC 가 그 노드면 로컬, 주 노드면 배포 별칭, 아니면 노드 이름.
on_node() { # on_node <node> <command>
  local node="$1" command="$2"
  if [[ "$node" == "$local_node" ]]; then
    bash -c "$command" < /dev/null
  elif [[ "$node" == "${NODE_PRIMARY_NODE_NAME:-}" ]]; then
    "${ssh_cmd[@]}" -n "$host" "$command"
  else
    "${ssh_cmd[@]}" -n "$node" "$command"
  fi
}

#: 설치된 래퍼의 지문. 파일이 없거나 머리줄이 없으면 빈 줄(=설치 필요), 읽지 못하면 rc≠0.
installed_digest() { # installed_digest <node>
  local out
  out="$(on_node "$1" "f=\"$WRAPPER_FILE\"; if [ -e \"\$f\" ]; then sed -n 's/^# wrapper-inputs: //p' \"\$f\"; fi")" || return 1
  out="${out//[[:space:]]/}"
  [[ -z "$out" || "$out" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s' "$out"
}

plan() { # plan <head>
  local head="$1" archive dir out node digest installed rc=0
  [[ "$head" =~ ^[0-9a-f]{40}$ ]] || { log "WRAPPER-PLAN-UNKNOWN: bad head"; return 1; }
  [[ -n "$host" ]] || { log "WRAPPER-PLAN-UNKNOWN: DEPLOY_SSH_HOST is unset"; return 1; }
  archive="$(mktemp)" || return 1
  if ! git -C "$REPO_ROOT" archive --format=tar "$head" > "$archive"; then
    rm -f -- "$archive"; log "WRAPPER-PLAN-UNKNOWN: git archive failed"; return 1
  fi
  dir="$("${ssh_cmd[@]}" -n "$host" 'mktemp -d /tmp/autophagy-wrapper-plan.XXXXXX')"
  dir="${dir//[[:space:]]/}"
  if [[ ! "$dir" =~ ^/tmp/autophagy-wrapper-plan\.[A-Za-z0-9]+$ ]]; then
    rm -f -- "$archive"; log "WRAPPER-PLAN-UNKNOWN: no staging directory on $host"; return 1
  fi
  if "${scp_cmd[@]}" "$archive" "$host:$dir/tree.tar"; then
    # 새 트리의 생성기를 그 트리 자신의 파일로 돌린다 — 프로브가 읽는 매니페스트·스킬 원본도 그 트리 것이다.
    out="$("${ssh_cmd[@]}" -n "$host" "cd '$dir' && mkdir tree && tar -xf tree.tar -C tree && for n in $(nodes | tr '\n' ' '); do d=\$(HEALTHCHECK_RELEASE_SOURCE_ROOT='$dir/tree' bash '$dir/tree/automation/healthcheck_probe_wrapper.sh' --inputs-digest \"\$n\" 2>/dev/null) || exit 1; printf '%s %s\n' \"\$n\" \"\$d\"; done")" || rc=1
  else
    rc=1
  fi
  "${ssh_cmd[@]}" -n "$host" "rm -rf -- '$dir'" || log "WRAPPER-PLAN-CLEANUP-FAIL: $host:$dir"
  rm -f -- "$archive"
  (( rc == 0 )) || { log "WRAPPER-PLAN-UNKNOWN: could not compute the release fingerprints"; return 1; }
  while read -r node digest; do
    [[ -n "$node" ]] || continue
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || { log "WRAPPER-PLAN-UNKNOWN: bad digest for $node"; return 1; }
    installed="$(installed_digest "$node")" \
      || { log "WRAPPER-PLAN-UNKNOWN: cannot read the installed wrapper on $node"; return 1; }
    if [[ "$installed" != "$digest" ]]; then
      log "WRAPPER-PLAN: $node installed=${installed:0:12} release=${digest:0:12} — 카드에 갱신 포함"
      printf '%s=%s\n' "$node" "$digest"
    fi
  done <<< "$out"
}

install_on() { # install_on <node>
  local node="$1" wrapper dir rc=0
  if [[ "$node" == "${NODE_PRIMARY_NODE_NAME:-}" ]]; then
    "${ssh_cmd[@]}" -n "$host" "bash '$runtime_root/automation/healthcheck_probe_wrapper.sh' --install '$node'" >&2
    return
  fi
  # 릴리스 트리가 없는 노드(RAG): 주 노드 릴리스가 생성한 래퍼를 파일로 받아 같은 설치기로 놓는다.
  wrapper="$(mktemp)" || return 1
  if ! "${ssh_cmd[@]}" -n "$host" "bash '$runtime_root/automation/healthcheck_probe_wrapper.sh' --print '$node'" > "$wrapper"; then
    rm -f -- "$wrapper"; return 1
  fi
  if [[ "$node" == "$local_node" ]]; then
    bash "$provisioner" --from-file "$wrapper" "$node" >&2 || rc=1
  else
    dir="$("${ssh_cmd[@]}" -n "$node" 'mktemp -d /tmp/autophagy-wrapper-install.XXXXXX')"
    dir="${dir//[[:space:]]/}"
    if [[ "$dir" =~ ^/tmp/autophagy-wrapper-install\.[A-Za-z0-9]+$ ]] \
      && "${scp_cmd[@]}" "$provisioner" "$wrapper" "$node:$dir/"; then
      "${ssh_cmd[@]}" -n "$node" "bash '$dir/$(basename "$provisioner")' --from-file '$dir/$(basename "$wrapper")' '$node'" >&2 || rc=1
    else
      rc=1
    fi
    [[ -z "$dir" ]] || "${ssh_cmd[@]}" -n "$node" "rm -rf -- '$dir'" || true
  fi
  rm -f -- "$wrapper"
  return "$rc"
}

refresh() { # refresh <head>
  local head="$1" node expected installed after rc
  [[ "$head" =~ ^[0-9a-f]{40}$ ]] || { log "WRAPPER-REFRESH-UNKNOWN: bad head"; return 1; }
  while read -r node; do
    expected="$("${ssh_cmd[@]}" -n "$host" "bash '$runtime_root/automation/healthcheck_probe_wrapper.sh' --inputs-digest '$node'" 2>/dev/null)"
    expected="${expected//[[:space:]]/}"
    if [[ ! "$expected" =~ ^[0-9a-f]{64}$ ]]; then
      printf 'WRAPPER-REFRESH|%s|failed|expected-unknown\n' "$node"; continue
    fi
    if ! installed="$(installed_digest "$node")"; then
      printf 'WRAPPER-REFRESH|%s|failed|installed-unreadable\n' "$node"; continue
    fi
    if [[ "$installed" == "$expected" ]]; then
      printf 'WRAPPER-REFRESH|%s|current|%s\n' "$node" "${expected:0:12}"; continue
    fi
    rc=0
    "${authorize_cmd[@]}" authorize-wrapper --node "$node" --hash "$expected" --head "$head" > /dev/null || rc=$?
    case "$rc" in
      0) ;;
      4) printf 'WRAPPER-REFRESH|%s|skipped|not-covered\n' "$node"; continue ;;
      8) printf 'WRAPPER-REFRESH|%s|skipped|cancelled\n' "$node"; continue ;;
      *) printf 'WRAPPER-REFRESH|%s|skipped|approval-unverified\n' "$node"; continue ;;
    esac
    if ! install_on "$node"; then
      printf 'WRAPPER-REFRESH|%s|failed|install-failed\n' "$node"; continue
    fi
    after="$(installed_digest "$node")" || after="unreadable"
    if [[ "$after" == "$expected" ]]; then
      printf 'WRAPPER-REFRESH|%s|installed|%s\n' "$node" "${expected:0:12}"
    else
      printf 'WRAPPER-REFRESH|%s|failed|mismatch:want=%s,got=%s\n' "$node" "${expected:0:12}" "${after:0:12}"
    fi
  done < <(nodes)
}

case "${1:-}" in
  plan) (( $# == 2 )) || { echo "usage: healthcheck_wrapper_release.sh plan|refresh <head>" >&2; exit 2; }; plan "$2" ;;
  refresh) (( $# == 2 )) || { echo "usage: healthcheck_wrapper_release.sh plan|refresh <head>" >&2; exit 2; }; refresh "$2" ;;
  *) echo "usage: healthcheck_wrapper_release.sh plan|refresh <head>" >&2; exit 2 ;;
esac

#!/usr/bin/env bash
# healthcheck 전용 ssh 키의 **강제명령 래퍼**를 릴리스에서 생성한다.
#
# 그 키는 authorized_keys 에서 `command="…/autophagy-healthcheck-probe"` 에 묶여 있고,
# 래퍼는 `SSH_ORIGINAL_COMMAND` 의 sha256 **정확 일치**만 통과시키고 나머지는 exit 126 이다.
# 즉 healthcheck 에 체크를 하나 더하면 그 체크가 내는 명령의 해시를 래퍼에도 넣어야 한다.
#
# 2026-08-20 실측에서 래퍼는 손으로 유지되고 있었고, 그 대가가 그대로 드러났다: 그 시점
# healthcheck 가 보내는 43개 명령 중 **23개가 목록에 없어 전부 거부**되고 있었고(그 중에는
# 다른 세션이 추가한 mailon·워처 체크와, 실패한 체크가 수리 티켓을 내는 경로가 포함된다),
# 반대로 아무도 더는 보내지 않는 **orphan 해시 14개**가 남아 허용 범위만 넓히고 있었다.
# 어느 쪽도 소리를 내지 않았다 — 거부는 그 체크 하나만 조용히 UNKNOWN/FAIL 로 만들고,
# orphan 은 정의상 아무 일도 하지 않기 때문이다.
#
# 그래서 목록을 **추론하지 않고 관측한다**: healthcheck 를 그대로 불러 `capture_on_node` 를
# 기록용으로 갈아끼우고 모든 체크를 한 바퀴 돌린다. 실제 ssh 는 한 번도 나가지 않는다.
# 프로브가 무슨 명령을 낼지 이 파일이 따로 알 필요가 없고, 그래서 프로브가 바뀌어도
# 여기를 고칠 일이 없다 — 손 유지보수를 없애는 것이 이 스크립트의 존재 이유다.
set -uo pipefail

WRAPPER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly WRAPPER_SCRIPT_DIR
readonly WRAPPER_VERSION=1

wrapper_die() { printf 'WRAPPER-GEN: %s\n' "$1" >&2; exit "${2:-1}"; }

#: 래퍼가 어떤 입력으로 만들어졌는지 한 줄로 남긴다. 드리프트 프로브는 이 값만 보고
#: 재생성이 필요한지 판정하므로, 매 5분마다 전수 기록을 다시 돌릴 필요가 없다.
#:
#: 체크 정의에는 이 트리의 경로가 박힌다(`<automation>/../configs/…` 매니페스트 인자). 그 경로를
#: 노드의 릴리스 경로로 정규화해 지문이 트리의 **위치**가 아니라 **내용**만 말하게 한다 —
#: 릴리스 승인 전에 새 트리를 노드의 임시 디렉터리에 풀어 기대 지문을 계산하는
#: `healthcheck_wrapper_release.sh` 가 수렴 뒤 `/srv/…` 에서 계산한 값과 같은 답을 얻어야 한다.
#: 그 경로에서 이미 돌던 생성기에게는 바이트 그대로라 설치된 래퍼의 지문은 바뀌지 않는다.
wrapper_inputs_digest() { # wrapper_inputs_digest <node>
  local node="$1" command_digest
  local live_dir="${NODE_RELEASE_CURRENT:-/srv/autophagy-agent-current}/automation/"
  command_digest="$(wrapper_recorded_commands_digest "$node")" || return 1
  {
    printf 'v%s\n%s\n' "$WRAPPER_VERSION" "$node"
    printf '%s\n' "${LIVE_CHECKS[@]//"$WRAPPER_SCRIPT_DIR/"/"$live_dir"}"
    printf 'recorded-probe-command-hashes:%s\n' "$command_digest"
    cat -- "${HEALTHCHECK_WATCHER_MANIFEST:-$WRAPPER_SCRIPT_DIR/../configs/watcher-deploy-manifest.txt}"
    cat -- "${HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST:-$WRAPPER_SCRIPT_DIR/../configs/runtime-package-manifest.txt}"
  } | sha256sum | cut -d' ' -f1
}

#: 실행하지 않고 기록만 한다. 성공을 돌려주는 것이 중요하다 — 실패를 돌려주면 프로브가
#: 조기 반환해 그 뒤에 낼 명령을 영영 못 본다(실측에서 이 차이로 목록이 잘렸다).
wrapper_record_commands() { # wrapper_record_commands <node> <outfile>
  local node="$1" out="$2" entry definition name
  : > "$out" || return 1
  capture_on_node() { printf '%s\0' "$2" >> "$out"; printf '%s' "active"; return 0; }

  # 기록 중임을 프로브들에게 알린다. 래퍼 드리프트 프로브는 기대 지문을 얻으려고 이
  # 생성기를 **새 프로세스로** 실행하는데, 그 프로세스가 다시 여기로 들어오면 재귀가
  # 끝나지 않는다(2026-08-31 실측: cron 실행 2 개 → ops 프로세스 436 개, 전부
  # `--inputs-digest`; 노드는 memory pressure critical). 그래서 기록 스윕 동안에는 그
  # 프로브가 명령만 남기고 생성기를 부르지 않는다 — 판정은 평시 틱이 한다.
  export HEALTHCHECK_WRAPPER_RECORDING=1

  # --suggest always examines the full catalog, including groups the current sweep has
  # excluded. The wrapper must allow those discovery commands or exit 126 becomes a false
  # "service absent" signal. Strip only the catalog's leading group field; the remaining
  # five-field record is the unchanged run_check contract.
  for entry in "${HEALTHCHECK_CHECK_CATALOG[@]}"; do
    definition="${entry#*|}"
    IFS='|' read -r _name _type check_node _rest <<< "$definition"
    [[ "$check_node" == "$node" ]] || continue
    run_check "$definition" >/dev/null 2>&1 || true
  done
  # 실패한 체크가 수리 티켓을 내는 경로도 같은 키로 나간다. 이것이 빠지면 체크는 실패를
  # 보고하지 못한 채 실패한다 — 2026-08-20 `release store usage` 가 정확히 그 상태였다.
  for entry in "${HEALTHCHECK_CHECK_CATALOG[@]}"; do
    definition="${entry#*|}"
    IFS='|' read -r name _type check_node _rest <<< "$definition"
    [[ "$check_node" == "$node" ]] || continue
    printf '%s\0' "$(healthcheck_repair_command "$name")" >> "$out"
  done
  unset HEALTHCHECK_WRAPPER_RECORDING
}

#: 사람이 검토할 수 있는 ``sha256<TAB>command`` 매니페스트. 명령 발견은 위의 관측
#: 한 경로뿐이고, 래퍼·입력 지문·healthcheck_allowlist_manifest.sh 가 이 출력을 공유한다.
wrapper_command_manifest() { # wrapper_command_manifest <node>
  local node="$1" out cmd
  local -a rows=()
  out="$(mktemp)" || return 1
  wrapper_record_commands "$node" "$out" || { rm -f -- "$out"; return 1; }
  while IFS= read -r -d '' cmd; do
    rows+=("$(printf '%s' "$cmd" | sha256sum | cut -d' ' -f1)"$'\t'"$cmd")
  done < "$out"
  rm -f -- "$out"
  ((${#rows[@]})) || return 1
  printf '%s\n' "${rows[@]}" | LC_ALL=C sort -u
}

#: 기록된 명령 본문의 해시 집합을 안정적으로 접는다. 허용 목록도 이 집합으로 만들어지므로
#: 체크 순서가 아니라 실제로 허용되는 명령만 지문에 반영한다.
wrapper_recorded_commands_digest() { # wrapper_recorded_commands_digest <node>
  local node="$1" manifest
  manifest="$(wrapper_command_manifest "$node")" || return 1
  printf '%s\n' "$manifest" | cut -f1 | sha256sum | cut -d' ' -f1
}

#: **노드에서 생성하라.** 기록은 프로브를 실제로 한 바퀴 돌리는 것이므로, 프로브가
#: 로컬 사유(파일 부재 등)로 조기 반환하면 그 뒤에 낼 명령은 기록되지 않는다. 개발
#: 체크아웃에서 만든 목록이 노드의 것보다 짧은 이유가 그것이다 — `--install` 은 노드의
#: 릴리스에서 돌린다. 드리프트 프로브가 지문으로 재생성 시점을 알려주므로, 목록이
#: 잘린 채 굳는 것은 다음 틱에 드러난다.
wrapper_print() { # wrapper_print <node>
  local node="$1" manifest row
  local -a hashes=()
  manifest="$(wrapper_command_manifest "$node")" \
    || wrapper_die "could not record commands for node=$node" 3
  while IFS= read -r row; do
    hashes+=("${row%%$'\t'*}")
  done <<< "$manifest"

  printf '#!/usr/bin/env bash\n'
  printf '# GENERATED by automation/healthcheck_probe_wrapper.sh — do not edit by hand.\n'
  printf '# Regenerate after ANY healthcheck change:\n'
  printf '#   bash <release>/automation/healthcheck_probe_wrapper.sh --install %s\n' "$node"
  printf '# wrapper-version: %s\n' "$WRAPPER_VERSION"
  printf '# wrapper-node: %s\n' "$node"
  printf '# wrapper-inputs: %s\n' "$(wrapper_inputs_digest "$node")"
  printf 'set -euo pipefail\n'
  printf 'readonly command_hash="$(printf %s "${SSH_ORIGINAL_COMMAND:-}" | sha256sum)"\n' "'%s'"
  printf 'case "${command_hash%%%% *}" in\n'
  printf '  %s) exec /bin/bash -c "$SSH_ORIGINAL_COMMAND" ;;\n' \
    "$(printf '%s|\\\n  ' "${hashes[@]::${#hashes[@]}-1}")${hashes[-1]}"
  printf "  *) printf 'healthcheck probe denied\\\\n' >&2; exit 126 ;;\n"
  printf 'esac\n'
}

main() {
  local mode="${1:-}" node="${2:-}"
  # healthcheck 는 LIVE_CHECKS·run_check·healthcheck_repair_command 를 모두 들고 있다.
  # 여기서 다시 정의하지 않는다 — 베끼는 순간 그 사본이 낡기 시작한다.
  # shellcheck source=automation/healthcheck.sh
  source "$WRAPPER_SCRIPT_DIR/healthcheck.sh" || wrapper_die "could not source healthcheck.sh"
  [[ -n "$node" ]] || node="$PRIMARY_NODE"

  case "$mode" in
    --print) wrapper_print "$node" ;;
    --inputs-digest) wrapper_inputs_digest "$node" ;;
    --install)
      exec "${HEALTHCHECK_PROVISIONER:-$WRAPPER_SCRIPT_DIR/provision-healthcheck-probe.sh}" "$node"
      ;;
    *) wrapper_die "usage: healthcheck_probe_wrapper.sh --print|--install|--inputs-digest [node]" 2 ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi

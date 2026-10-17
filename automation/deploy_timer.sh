#!/usr/bin/env bash
# no-agent 잡을 Hermes cron 대신 계정의 systemd 사용자 타이머로 띄운다(P2) — 선언 `v2:cron;…;runner=systemd`.
#
# 실제 일은 노드 릴리스 트리(`NODE_RELEASE_CURRENT`)의 `automation/cron_timer.py` 가 계정 안에서 한다:
# 잠금 → 같은 이름의 Hermes 잡을 일시정지하고 **다시 읽어 paused 확인** → 유닛 두 개 쓰기 → daemon-reload →
# 타이머 enable(첫 실행은 다음 달력 시각) → 재확인. 그다음 여기서 `check` 로 한 번 더 읽어 ok 가 아니면 실패다.
# Hermes 잡은 지우지 않는다. 되돌리기는 `revert_timer <이름>`(타이머 끄고 이 수렴기가 멈춘 잡만 resume).
#
# 호출자는 `run_agent <script>` 를 정의해 둔 상태여야 한다(converge_cron 과 같다).
# 종료코드: 0 맞춤 · 2 인자 거부(원격 호출 전) · 4 Hermes 목록 판독 불가(아무것도 안 바꿈) · 5 변경 실패/사후 불일치 ·
#           6 잠금 · 7 판정상 거부(이름 중복·스크립트 미배포).

_CONVERGE_TIMER_SAFE='^[A-Za-z0-9_.*/, -]*$'

_timer_remote() { # _timer_remote <verb> <args…> → 원격 스크립트 한 줄
  # DEPLOY_TIMER_RUNTIME: 시험 리그가 노드 릴리스 트리 대신 체크아웃을 가리키는 이음새(운영에서는 쓰지 않는다).
  local runtime="${DEPLOY_TIMER_RUNTIME:-${NODE_RELEASE_CURRENT:-/srv/autophagy-agent-current}}" line
  printf -v line 'cd "$HOME" && PYTHONPATH=%q /usr/bin/python3 -B -m automation.cron_timer' "$runtime"
  printf '%s' "$line"
  printf ' %q' "$@"
}

converge_timer() { # converge_timer <name> <schedule> <script> <local|discord> [env]
  local -
  set +e
  local arg out rc
  if (( $# != 4 && $# != 5 )); then
    printf 'CRON-TIMER-BLOCK: usage: converge_timer <name> <schedule> <script> <deliver> [env]\n' >&2
    return 2
  fi
  if [[ "$4" != local && "$4" != discord ]]; then
    printf 'CRON-TIMER-BLOCK: %s delivers to %q; a timer delivers to local or discord only\n' "$1" "$4" >&2
    return 2
  fi
  for arg in "$@"; do
    if [[ ! "$arg" =~ $_CONVERGE_TIMER_SAFE ]]; then
      printf 'CRON-TIMER-BLOCK: unsafe argument %q; nothing was sent\n' "$arg" >&2
      return 2
    fi
  done
  out="$(run_agent "$(_timer_remote converge --name "$1" --schedule "$2" --script "$3" --env "${5-}" --deliver "$4")" </dev/null 2>&1)"
  rc=$?
  printf '%s\n' "$out"
  (( rc == 0 )) || return "$rc"
  out="$(run_agent "$(_timer_remote check --name "$1" --schedule "$2" --script "$3" --env "${5-}" --deliver "$4")" </dev/null 2>&1)"
  printf '%s\n' "$out"
  if [[ "$out" != *"TIMER-CHECK state=ok "* ]]; then
    printf 'CRON-TIMER-BLOCK: %s read-back is not ok after converge\n' "$1" >&2
    return 5
  fi
}

revert_timer() { # revert_timer <name>
  if (( $# != 1 )) || [[ ! "$1" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    printf 'CRON-TIMER-BLOCK: usage: revert_timer <name>\n' >&2
    return 2
  fi
  run_agent "$(_timer_remote revert --name "$1")" </dev/null
}

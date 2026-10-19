#!/usr/bin/env bash
# healthcheck 전용 ssh 키의 강제명령 래퍼가 지금의 체크 목록과 맞는지 본다.
#
# 래퍼는 명령 해시 정확 일치만 통과시키므로, 체크를 추가하고 래퍼를 재생성하지 않으면
# 그 체크는 **조용히 exit 126** 을 받는다. 조용한 이유는 구조적이다 — 거부당한 프로브는
# 자기 하나만 UNKNOWN/FAIL 이 되고, 그 실패가 수리 티켓을 내려 해도 티켓 명령 역시
# 같은 이유로 거부되기 때문에 밖으로 나가는 신호가 없다.
#
# 2026-08-20 실측: 보내지는 명령 43개 중 23개가 목록 밖이었고, 더는 쓰이지 않는 해시
# 14개가 남아 있었다. 두 방향 모두 아무 경보도 내지 않았다.
#
# 판정은 래퍼 헤더의 `# wrapper-inputs:` 한 줄로 한다. 생성기가 그 값을 LIVE_CHECKS 와
# 워처 매니페스트에서 계산해 박아두므로, 매 틱 전수 기록을 다시 돌릴 필요가 없다.
wrapper_probe_log() { printf '[healthcheck-wrapper] %s\n' "$*" >&2; }

wrapper_probe_guidance() { # wrapper_probe_guidance <node>
  # Generated as the healthcheck account: only its view matches this probe's expected digest.
  wrapper_probe_log \
    "regenerate on the node as the operator (no root needed — the operator owns the file):"
  wrapper_probe_log \
    "  f=\$(mktemp) && sudo -n -u ${NODE_OPS_ACCOUNT:-ops} -H bash /srv/autophagy-agent-current/automation/healthcheck_probe_wrapper.sh --print $1 > \"\$f\" && bash /srv/autophagy-agent-current/automation/provision-healthcheck-probe.sh --from-file \"\$f\" $1; rm -f \"\$f\""
}

probe_healthcheck_wrapper_current() {
  local node="$1" _account="$2" target="$3"
  local source_root="${HEALTHCHECK_RELEASE_SOURCE_ROOT:-/srv/autophagy-agent-current}"
  local generator="$source_root/automation/healthcheck_probe_wrapper.sh"
  local expected installed
  # 이 프로브가 노드에 내는 유일한 명령. 기록 경로와 실제 호출이 **같은 문자열**이어야
  # 한다 — 래퍼는 sha256 정확 일치만 통과시키므로 한 글자만 달라도 exit 126 이다.
  local probe_command="sed -n 's/^# wrapper-inputs: //p' \"\$HOME/.local/libexec/autophagy-healthcheck-probe\""

  # 허용목록 기록 스윕(`--inputs-digest`) 안에서는 명령만 남기고 **생성기를 부르지 않는다**.
  # 부르면 그 프로세스가 다시 기록 스윕을 돌리고 그 안에서 이 프로브가 또 생성기를 부른다 —
  # 끝없는 재귀다. 2026-08-31 실측: cron 실행 2 개가 ops 프로세스 436 개로 불어났고 전부
  # `--inputs-digest` 였다. healthcheck 한 번이 몇 시간씩 걸리고(중앙값 4048 초), 노드가
  # memory pressure critical 에 닿고, 기대 지문이 쓰레기가 되어 이 체크가 영구 FAIL(티켓
  # t_d2ac107a ~1946 회)이던 원인이 모두 이것이다. 기록 모드에서 판정은 필요 없다 — 이
  # 스윕의 목적은 "무슨 명령이 나가는가"를 관측하는 것뿐이므로 성공으로 돌려준다.
  if [[ "${HEALTHCHECK_WRAPPER_RECORDING:-}" == "1" ]]; then
    capture_on_node "$node" "$probe_command" >/dev/null
    return 0
  fi

  if [[ ! -r "$generator" ]]; then
    wrapper_probe_log "WRAPPER-DRIFT-UNKNOWN: generator missing at $generator"
    return 1
  fi
  if ! expected="$(bash "$generator" --inputs-digest "$node" 2>/dev/null)" \
    || [[ ! "$expected" =~ ^[0-9a-f]{64}$ ]]; then
    wrapper_probe_log "WRAPPER-DRIFT-UNKNOWN: could not compute the expected inputs digest"
    return 1
  fi

  # 래퍼 자신이 이 명령을 거부하면 그것이 곧 답이다 — 재생성되지 않았다는 뜻이다.
  # (거부와 노드 불통을 여기서 가르지 않는다: 어느 쪽이든 사람이 봐야 하고, 안내는 같다.)
  if ! installed="$(capture_on_node "$node" "$probe_command")"; then
    wrapper_probe_log "WRAPPER-DRIFT: the wrapper rejected this probe or the node is unreachable"
    wrapper_probe_guidance "$node"
    return 1
  fi
  installed="${installed//[[:space:]]/}"

  if [[ -z "$installed" ]]; then
    wrapper_probe_log "WRAPPER-DRIFT: the installed wrapper carries no provenance header"
    wrapper_probe_log "  it predates the generator and was maintained by hand — regenerate it"
    wrapper_probe_guidance "$node"
    return 1
  fi
  if [[ "$installed" != "$expected" ]]; then
    if [[ -n "${NODE_AGENT_ACCOUNT:-}" ]] && \
        sudo -n -u "$NODE_AGENT_ACCOUNT" -H env PYTHONPATH="$source_root" \
        python3 -m automation.healthcheck_pending \
        "${NODE_AGENT_HOME:-$HOME}/.hermes/skill-gate/pending/release.json" \
        "$node" "$expected" "$source_root" 2>/dev/null; then
      wrapper_probe_log "갱신 대기: 현재 릴리스 카드가 이 노드의 허용 목록 갱신을 포함함"
    fi
    wrapper_probe_log "WRAPPER-DRIFT: the wrapper was built from an older check list"
    wrapper_probe_log "  installed=${installed:0:12} expected=${expected:0:12}"
    wrapper_probe_guidance "$node"
    return 1
  fi

  wrapper_probe_log "WRAPPER-PASS: the allowlist matches the current checks (${expected:0:12})"
  : "${target:-}"
}

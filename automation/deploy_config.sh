#!/usr/bin/env bash
# 계정 `~/.hermes/config.yaml` 의 선언된 키를 **릴리스 트리의 코드로** 맞추고 다시 읽어 확인한다.
#
# 왜 공용 헬퍼인가: Hermes 설정은 손 편집이 유일한 길이었고(2026-10-14 메모리 상한 조사: 리포 어디에도
# `user_char_limit` 이 없었다) 바꾼 값이 다음 재설치·다른 계정에서 조용히 갈라졌다. 선언
# `v2:config;algorithm=<이름>` 이 원하는 값을 정하고 릴리스 관측기가 어긋남을 찾으면, 배포기는 이 함수
# 하나로만 그 파일을 쓴다(`tests/unit/test_deploy_write_inventory.py` 가 헬퍼 목록을 강제한다).
#
# 실제 편집은 노드의 릴리스 트리(`NODE_RELEASE_CURRENT`)에 있는 알고리즘 모듈이 계정 안에서 한다 —
# 백업 → 같은 디렉터리 임시 파일 → rename, 선언한 키 말고는 바이트를 바꾸지 않는다. 그다음 `--check`
# 로 다시 읽어 맞지 않으면 실패다(쓰기가 0 을 돌려줘도 확인 없이는 성공이 아니다 — push_file 과 같은 규율).
# 원격 명령은 stdin 을 /dev/null 로 막는다(호출자의 stdin 을 먹지 않게).
#
# 호출자는 `run_account <account> <script>` 를 정의해 둔 상태여야 한다.
# 종료코드: 0 맞춤 · 2 인자 거부(원격 호출 전) · 5 쓰기 실패 또는 사후 확인 불일치 · 7 모듈이 거부(예: 상한 낮추기).

_CONVERGE_CONFIG_SAFE='^[A-Za-z0-9_./-]+$'

_converge_config_module() { # _converge_config_module <algorithm> → "<module> <destination>"
  case "$1" in
    hermes-memory-limits-v1) printf '%s' "automation.hermes_memory_config .hermes/config.yaml" ;;
    hermes-soul-recall-v2) printf '%s' "automation.hermes_soul_recall .hermes/SOUL.md" ;;
    hermes-memory-profile-with-recall-v1) printf '%s' "automation.memory_provider.config .hermes/config.yaml" ;;
    *) return 1 ;;
  esac
}

converge_config() { # converge_config <account> <destination-relative-to-HOME> <algorithm>
  local -
  set +e
  local account="$1" destination="$2" algorithm="$3" module target runtime rc out
  runtime="${NODE_RELEASE_CURRENT:-/srv/autophagy-agent-current}"
  if [[ ! "$account" =~ $_CONVERGE_CONFIG_SAFE || ! "$runtime" =~ $_CONVERGE_CONFIG_SAFE ]]; then
    printf 'DEPLOY-BLOCK: converge_config refuses unsafe account or runtime path.\n' >&2
    return 2
  fi
  if ! target="$(_converge_config_module "$algorithm")"; then
    printf 'DEPLOY-BLOCK: converge_config has no module for algorithm %q.\n' "$algorithm" >&2
    return 2
  fi
  module="${target%% *}"
  if [[ "$destination" != "${target#* }" ]]; then
    printf 'DEPLOY-BLOCK: algorithm %q writes %s, not %q.\n' "$algorithm" "${target#* }" "$destination" >&2
    return 2
  fi
  out="$(run_account "$account" "PYTHONPATH=$(printf '%q' "$runtime") python3 -B -m $module --apply" </dev/null 2>&1)"
  rc=$?
  printf '%s\n' "$out"
  if [[ $rc -eq 7 ]]; then
    printf 'DEPLOY-BLOCK: %s %s refused by %s (rc 7).\n' "$account" "$destination" "$module" >&2
    return 7
  fi
  if [[ $rc -ne 0 ]]; then
    printf 'DEPLOY-BLOCK: %s %s apply failed (rc %s).\n' "$account" "$destination" "$rc" >&2
    return 5
  fi
  out="$(run_account "$account" "PYTHONPATH=$(printf '%q' "$runtime") python3 -B -m $module --check" </dev/null 2>&1)"
  rc=$?
  printf '%s\n' "$out"
  if [[ $rc -ne 0 ]]; then
    printf 'DEPLOY-BLOCK: %s %s read-back does not match (rc %s).\n' "$account" "$destination" "$rc" >&2
    return 5
  fi
  return 0
}

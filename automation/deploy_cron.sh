#!/usr/bin/env bash
# no-agent cron 등록을 **목록을 읽은 뒤에만** 만들거나 제자리에서 고친다.
#
# 왜 공용 헬퍼인가: 배포기마다 등록 줄이 따로 있었고 셋이 서로 달랐다 — 어떤 것은 `--all`
# 없이 목록을 읽어 일시정지된 잡을 못 보고 같은 잡을 하나 더 만들 수 있었고
# (`automation/memory_curator/deploy.sh` 의 설명), 어떤 것은 주기를 고치지 않았으며
# (`skills/mail/deploy.sh`), 이름이 둘인 잡이나 에이전트 모드 잡을 가리지 않았다.
#
# 규칙(원격 스크립트 한 번 안에서, 계정별 잠금 아래):
#   이름이 같은 잡 0개 — 같은 Script 가 다른 이름으로 이미 있으면 막고(rc 7), 아니면 create.
#   1개 — 에이전트 모드면 건드리지 않고(rc 7), 주기(공백 정규화)·전달 대상·스크립트가 다를 때만 edit.
#   2개 이상 — 아무것도 바꾸지 않고 막는다(rc 7).
#   바꿨으면 목록을 다시 읽어 그 이름이 정확히 1개이고 값이 맞는지 확인한다(아니면 rc 5).
# 삭제·재생성·일시정지 상태 변경은 하지 않는다(id 와 이력을 지킨다).
#
# 종료코드: 2 인자 거부(원격 호출 전) · 4 목록 실패/판독 불가(아무것도 만들지 않음) ·
#           5 변경 실패 또는 사후 확인 불일치 · 6 잠금 획득 실패 · 7 판정상 거부.
# 모든 hermes 호출은 `timeout` 아래에서 돈다(기본 120초, 시험용 `DEPLOY_CRON_TIMEOUT`) — 멈춘 CLI 가
# 배포를 영원히 붙잡지 못하게. 목록 시간 초과는 rc 4, create/edit 시간 초과는 다시 읽은 목록으로 판정한다.
#
# 호출자는 `run_agent <script>` 를 정의해 둔 상태여야 한다(계정·호스트가 배포기마다 다르다).
# 인자는 안전 문자 검사를 통과한 뒤 `printf %q` 로만 원격 스크립트에 실린다.

_CONVERGE_CRON_SAFE='^[A-Za-z0-9_.*/, -]+$'

#: `hermes cron list --all` 을 잡당 한 줄(id·이름·주기·전달·스크립트·모드·스킬·상태, \037 구분)로
#: 바꾼다. 목록 명령이 실패·시간 초과하거나 형식이 기대에서 벗어나면 rc 1 — 없다고 읽지 않는다.
#: 문법은 벤더 `hermes_cli/cron.py::cron_list` 출력이고 릴리스 관측기
#: (`automation/deploy_all_cron_listing.parse_cron_listing`)와 같은 계약이다 — 두 해석기는
#: `tests/unit/test_cron_listing_parser_parity.py` 가 같은 입력 묶음으로 묶는다(계약 전문은 그 docstring).
#: 2칸 들여쓴 줄이 머리 줄 모양이 아니거나(대문자 id·꼬리말·모르는 상태·잘린 머리 줄) 한 블록에
#: 같은 키가 두 번 나오면 판독 불가다 — 그 줄을 건너뛰면 다음 잡의 필드가 앞 잡의 id 에 붙어
#: 엉뚱한 잡을 고치게 된다(2026-10-01 검증 재현: mail-daily-digest 를 triage 스크립트로 덮어쓸 뻔했다).
#: 빈 출력·제목 없는 목록·블록 중간에서 끝난 목록도 판독 불가다 — 0개로 읽으면 잡을 새로 만든다.
#: 끝 개행을 지키려고 출력 뒤에 표지 `x` 를 붙여 받는다(`$(…)` 는 끝 개행을 지운다).
_converge_cron_jobs() { # _converge_cron_jobs <timeout seconds>
  local listing us nl
  us="$(printf '\037')"
  nl="$(printf '\nx')"; nl="${nl%x}"
  listing="$(timeout -k 10 "$1" hermes cron list --all && printf x)" || return 1
  listing="${listing%x}"
  [[ "$listing" == *"$nl" ]] || return 1
  printf '%s' "$listing" | awk -v us="$us" '
    function trim(s) { gsub(/[ \t]+/, " ", s); sub(/^ /, "", s); sub(/ $/, "", s); return s }
    function flush() {
      if (id != "") {
        if (nm == "" || sc == "" || dl == "" || (md != "" && sp == "")) bad = 1
        print id us nm us sc us dl us sp us md us sk us st
      }
      id = nm = sc = dl = sp = md = sk = st = ""
      split("", seen)
    }
    BEGIN { ended = 1 }
    /^  [0-9a-f]+ \[(active|paused|completed|disabled)\][ \t]*$/ {
      if (!titled || !ended) bad = 1
      flush(); id = $1; st = substr($2, 2, length($2) - 2); jobs++; ended = 0; next
    }
    index($0, "  ⚠  Gateway is not running") == 1 { next }
    index($0, "  ⚠  Scheduler is not ready") == 1 { next }
    /^  [^ ]/ { bad = 1; next }
    /^    [A-Z][A-Za-z ]*:/ {
      if (id == "") { bad = 1; next }
      ended = 0
      key = $0; sub(/:.*/, "", key); key = trim(key)
      val = $0; sub(/^[^:]*:/, "", val); val = trim(val)
      if (key in seen) bad = 1
      seen[key] = 1
      if (key == "Name") nm = val
      else if (key == "Schedule") sc = val
      else if (key == "Deliver") dl = val
      else if (key == "Script") sp = val
      else if (key == "Mode") md = val
      else if (key == "Skills") sk = val
      next
    }
    /^$/ { ended = 1; next }
    jobs == 0 && /Scheduled Jobs/ { titled = 1 }
    jobs == 0 && $0 == "No scheduled jobs." { empty = 1 }
    END {
      flush()
      if (jobs == 0 && !(empty && !titled)) bad = 1
      if (jobs > 0 && !ended) bad = 1
      exit (bad ? 1 : 0)
    }'
}

#: 원격에서 도는 본문. `declare -f` 로 실려 가므로 이 파일의 다른 함수에 기대지 않는다
#: (`_converge_cron_jobs` 는 함께 실린다).
_converge_cron_remote() {
  local name="$1" schedule="$2" script="$3" deliver="$4" limit="$5"
  local us jobs mine count other id cur_sched cur_deliver cur_script cur_mode action want_sched rc
  us="$(printf '\037')"
  PATH="$HOME/.local/bin:$PATH"
  mkdir -p "$HOME/.hermes/cron" || exit 6
  exec 9>"$HOME/.hermes/cron/.autophagy-converge.lock" || exit 6
  if ! flock -w 60 9; then
    printf 'CRON-CONVERGE-BLOCK: another cron convergence holds the lock; nothing changed for %s\n' "$name" >&2
    exit 6
  fi

  if ! jobs="$(_converge_cron_jobs "$limit")"; then
    printf 'CRON-CONVERGE-BLOCK: hermes cron list --all failed, timed out or was unreadable; nothing changed for %s\n' "$name" >&2
    exit 4
  fi
  want_sched="$(printf '%s\n' "$schedule" | awk '{ $1 = $1; print }')"
  mine="$(printf '%s\n' "$jobs" | awk -F "$us" -v n="$name" 'NF && $2 == n')"
  count=0
  [[ -n "$mine" ]] && count="$(printf '%s\n' "$mine" | wc -l)"

  if (( count > 1 )); then
    printf 'CRON-CONVERGE-BLOCK: duplicate job name %s\n' "$name" >&2
    exit 7
  elif (( count == 0 )); then
    other="$(printf '%s\n' "$jobs" | awk -F "$us" -v s="$script" 'NF && $5 == s { print $2; exit }')"
    if [[ -n "$other" ]]; then
      printf 'CRON-CONVERGE-BLOCK: script %s is already registered as %s\n' "$script" "$other" >&2
      exit 7
    fi
    timeout -k 10 "$limit" hermes cron create "$schedule" --name "$name" --no-agent --script "$script" --deliver "$deliver"
    rc=$?
    action=created
  else
    IFS="$us" read -r id _ cur_sched cur_deliver cur_script cur_mode _ <<<"$mine"
    if [[ "$cur_mode" != no-agent* ]]; then
      printf 'CRON-CONVERGE-BLOCK: job %s (%s) is not a no-agent job; it is left untouched\n' "$name" "$id" >&2
      exit 7
    fi
    if [[ "$cur_sched" == "$want_sched" && "$cur_deliver" == "$deliver" && "$cur_script" == "$script" ]]; then
      printf 'CRON-CONVERGE-OK name=%s action=unchanged id=%s\n' "$name" "$id"
      exit 0
    fi
    timeout -k 10 "$limit" hermes cron edit "$id" --schedule "$schedule" --deliver "$deliver" --no-agent --script "$script"
    rc=$?
    action=edited
  fi
  # 시간 초과(124, -k 뒤 137)는 변경이 일어났는지 모른다는 뜻이다 — 다시 읽은 목록만으로 판정한다.
  if (( rc == 124 || rc == 137 )); then
    printf 'CRON-CONVERGE-WARN: hermes call for %s (%s) timed out after %ss; judging by a fresh listing\n' \
      "$name" "$action" "$limit" >&2
  elif (( rc != 0 )); then
    exit 5
  fi

  if ! jobs="$(_converge_cron_jobs "$limit")"; then
    printf 'CRON-CONVERGE-BLOCK: could not re-read the cron list after %s %s\n' "$action" "$name" >&2
    exit 5
  fi
  mine="$(printf '%s\n' "$jobs" | awk -F "$us" -v n="$name" 'NF && $2 == n')"
  count=0
  [[ -n "$mine" ]] && count="$(printf '%s\n' "$mine" | wc -l)"
  if (( count == 1 )); then
    IFS="$us" read -r id _ cur_sched cur_deliver cur_script cur_mode _ <<<"$mine"
  fi
  if (( count != 1 )) || [[ "$cur_sched" != "$want_sched" || "$cur_deliver" != "$deliver" \
      || "$cur_script" != "$script" || "$cur_mode" != no-agent* ]]; then
    printf 'CRON-CONVERGE-BLOCK: after %s, %s is not exactly one job with the wanted values (found %s)\n' \
      "$action" "$name" "$count" >&2
    exit 5
  fi
  printf 'CRON-CONVERGE-OK name=%s action=%s id=%s\n' "$name" "$action" "$id"
}

converge_cron() { # converge_cron <name> <schedule> <script> <deliver>
  local arg remote limit="${DEPLOY_CRON_TIMEOUT-120}"
  if [[ ! "$limit" =~ ^[1-9][0-9]{0,3}$ ]]; then
    printf 'CRON-CONVERGE-BLOCK: DEPLOY_CRON_TIMEOUT must be 1-9999 seconds; nothing was sent\n' >&2
    return 2
  fi
  if (( $# != 4 )); then
    printf 'CRON-CONVERGE-BLOCK: usage: converge_cron <name> <schedule> <script> <deliver>\n' >&2
    return 2
  fi
  for arg in "$@"; do
    if [[ ! "$arg" =~ $_CONVERGE_CRON_SAFE ]]; then
      printf 'CRON-CONVERGE-BLOCK: unsafe argument %q; nothing was sent\n' "$arg" >&2
      return 2
    fi
  done
  remote="$(declare -f _converge_cron_jobs _converge_cron_remote)"
  printf -v remote '%s\n_converge_cron_remote %q %q %q %q %q\n' "$remote" "$@" "$limit"
  run_agent "$remote" < /dev/null
}

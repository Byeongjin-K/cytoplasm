#!/usr/bin/env bash
# automation/install/quickstart.sh — 처음 설치하는 사람을 위한 편의 래퍼.
#
# 이 스크립트는 설치 로직을 하나도 갖고 있지 않다. 하는 일은 다섯뿐이다:
#   ① `python3 -m automation.install … --dry-run`을 먼저 돌려 계획을 그대로 보여주고
#   ② 그 출력에서 계산한 요약을 낸 뒤 사람의 명시적 확인을 받고
#   ③ 같은 인자를 `--dry-run`만 빼고 sudo로 다시 돌리고
#   ④ 설치기가 마지막에 돌린 healthcheck 종료 게이트의 판정을 요약하고
#   ⑤ 설치가 끝났으면 `python3 -m automation.doctor`로 사람이 승인할 연결을 안내한다.
# 절차의 단일 진실은 여전히 docs/guide/install.md이며, 이 래퍼는 그 문서의 명령을
# 대체하지 않는다 — 같은 명령을 그대로 조립해 실행할 뿐이다.
#
# WHY 확인을 강제하는가. 처음 받는 사람이 실행하는 첫 명령이 `sudo`로 계정·sudoers·
# systemd 타이머를 만든다. 무엇이 만들어질지 보지 못한 채 권한이 올라가는 경로를
# 만들지 않는다. `--yes`는 스크립트 실행용 탈출구이지 기본값이 아니다.
#
# WHY --config를 필수로 받는가. 설치기는 `--config`를 생략하면 예제 기본값으로
# 계획을 만든다 — 그 예제는 **다른 사람의 프로덕션 값**이다(docs/follow-ups.md).
# 제3자 설치에서 그 기본값이 쓰이는 경로를 이 래퍼에서는 아예 없앤다.
#
# WHY healthcheck를 따로 돌리지 않는가. 설치기의 **마지막 액션이 그 게이트**다
# (`check healthcheck` → ops 계정으로 automation/healthcheck.sh 실행). 래퍼가 한 번 더
# 돌리면 같은 판정을 두 곳이 내리게 되고, 그 둘이 어긋나는 날 무엇을 믿을지 알 수
# 없어진다. 그래서 여기서는 실행하지 않고 설치기가 낸 판정 줄을 읽어서 요약한다.
#
# WHY dry-run 출력을 다시 서술하지 않는가. 요약은 전부 실제 출력에서 세어 만든다.
# 사람이 손으로 적은 설명은 설치기가 바뀌는 순간 조용히 낡는다.
#
# 사용:
#   automation/install/quickstart.sh \
#       --profile core \
#       --config <node.toml> \
#       --update-trust-key <bundle>/update-trust.pub \
#       [--expect-update-trust-fingerprint 'SHA256:<공지된-지문>'] [옵션…]
#
# 옵션 (아래 7개는 설치기와 **같은 이름으로 그대로 전달**된다):
#   --profile NAME                         레거시 경로만: core|rag|report-hub|full
#   --config PATH                           (필수)
#   --update-trust-key PATH                 (필수)
#   --expect-update-trust-fingerprint STR
#   --discord-config PATH
#   --group-roster PATH
#   --expect-group-skill-fingerprint STR
#   --with-component NAME                   (반복 가능)
# 래퍼 자신의 옵션:
#   --dry-run-only   계획만 보고 끝낸다 (sudo 실행 없음)
#   --yes            확인 프롬프트를 건너뛴다 (비대화 실행 전용)
#   -h, --help
#
# Env:
#   DISCORD_BOT_TOKEN   설치기의 discord-readiness 체크가 환경변수에서만 읽는다(없으면 WARN).
#                       sudo는 기본적으로 환경을 지우므로 이 래퍼가 이 변수 하나만
#                       보존을 시도하고, sudoers가 거부하면 조용히 넘어가지 않고 알린다.
#   QUICKSTART_LOG_DIR  로그를 둘 디렉터리 (기본: mktemp -d, 0700)
#
# Exit codes: 0 ok | 1 설치기가 낸 실패(rc 그대로 전파) | 2 사용법·전제 오류
#             3 사용자가 확인을 거부(아무것도 실행하지 않음)
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 기본 경로는 도리 마법사다. 아래 기존 노드 설치는 명시적 레거시 프로필에만 남긴다.
legacy=0
previous=""
for arg in "$@"; do
  if [[ "$previous" == --profile ]]; then
    case "$arg" in
      core|rag|report-hub|full) legacy=1 ;;
    esac
  fi
  previous="$arg"
done
if [[ "$legacy" -eq 0 ]]; then
  cd "$REPO_ROOT"
  exec python3 -m automation.install.wizard "$@"
fi

log()  { printf '[quickstart] %s\n' "$*"; }
warn() { printf '[quickstart] WARN: %s\n' "$*" >&2; }
die()  { printf '[quickstart] ERROR: %s\n' "$1" >&2; exit "${2:-2}"; }

# 사용법은 위 헤더가 단일 진실이다 — 줄 번호로 잘라내면 헤더가 움직일 때 조용히 어긋난다.
usage() {
  awk '/^# 사용:/{p=1} p && /^#/{sub(/^# ?/, "");
  print;
next} p{exit}' "${BASH_SOURCE[0]}"
}

# ---------------------------------------------------------------- 인자 파싱
config=""
trust_key=""
dry_run_only=0
assume_yes=0
passthrough=()

require_value() {
  [[ $# -ge 2 && -n "${2:-}" ]] || die "$1 에 값이 필요하다"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      require_value "$1" "${2:-}"; config="$2"; shift 2 ;;
    --update-trust-key)
      require_value "$1" "${2:-}"; trust_key="$2"; shift 2 ;;
    --expect-update-trust-fingerprint|--discord-config|--group-roster|\
    --expect-group-skill-fingerprint|--with-component|--profile)
      require_value "$1" "${2:-}"; passthrough+=("$1" "$2"); shift 2 ;;
    --dry-run-only) dry_run_only=1; shift ;;
    --yes|--non-interactive) assume_yes=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --dry-run)
      die "--dry-run은 이 래퍼가 항상 먼저 수행한다. 계획만 보려면 --dry-run-only를 쓴다" ;;
    *) die "모르는 옵션: $1 (사용법은 --help)" ;;
  esac
done

# ------------------------------------------------------------ 전제 확인(시끄럽게)
[[ -f "$REPO_ROOT/automation/install/installer.py" ]] \
  || die "저장소 체크아웃 안에서 실행해야 한다. 여기에는 설치기가 없다: $REPO_ROOT"
command -v python3 >/dev/null 2>&1 \
  || die "python3가 없다. 설치기는 stdlib만 쓰지만 인터프리터는 필요하다"

[[ -n "$config" ]] \
  || die "--config가 필요하다. 생략하면 설치기가 예제(=남의 프로덕션) 기본값을 쓴다. 만드는 법: docs/guide/install.md §3"
[[ -n "$trust_key" ]] \
  || die "--update-trust-key가 필요하다. 릴리스 번들의 update-trust.pub 경로를 준다"
[[ -r "$config" ]]    || die "노드 config를 읽을 수 없다: $config"
[[ -r "$trust_key" ]] || die "업데이트 신뢰키를 읽을 수 없다: $trust_key"

expects_fingerprint=0
for arg in ${passthrough+"${passthrough[@]}"}; do
  [[ "$arg" == "--expect-update-trust-fingerprint" ]] && expects_fingerprint=1
done
if [[ "$expects_fingerprint" -eq 0 ]]; then
  warn "--expect-update-trust-fingerprint를 주지 않았다. 지문 대조가 기계 판정에서 빠지고"
  warn "  종료 게이트에서 PASS 대신 WARN이 된다. 번들 지문은 이렇게 본다:"
  warn "    python3 automation/install/trust_key_bootstrap.py fingerprint --key $trust_key"
  warn "  그 값을 공개 저장소 README·릴리스 노트의 공지 지문과 눈으로 대조한다."
fi

log_dir="${QUICKSTART_LOG_DIR:-$(mktemp -d)}"
mkdir -p "$log_dir"
chmod 700 "$log_dir"
readonly DRY_LOG="$log_dir/01-dry-run.log"
readonly RUN_LOG="$log_dir/02-install.log"

installer_args=(--config "$config" --update-trust-key "$trust_key")
installer_args+=(${passthrough+"${passthrough[@]}"})

# ------------------------------------------------------------------ ① dry-run
log "① 계획 확인 — 쓰기 없음, root 불필요"
log "   python3 -m automation.install ${installer_args[*]} --dry-run"
echo
cd "$REPO_ROOT"
set +e
python3 -m automation.install "${installer_args[@]}" --dry-run 2>&1 | tee "$DRY_LOG"
dry_rc=${PIPESTATUS[0]}
set -e
echo

if [[ "$dry_rc" -ne 0 ]]; then
  log "① 실패 (rc=$dry_rc). **아무것도 쓰이지 않았다.** 설치기가 지목한 항목:"
  grep -E '^(INSTALL-BLOCK|USAGE-ERROR|TRUST-KEY-|GROUP-[A-Z-]+:|\[FAIL\]|\[WARN\]|--- )' "$DRY_LOG" \
    | sed 's/^/    /' || true
  log "고치는 법: docs/guide/third-party-runtime-prereqs.md · docs/guide/install.md §8"
  log "전문: $DRY_LOG"
  exit "$dry_rc"
fi

# 요약은 방금 나온 출력에서 센다 — 손으로 적은 설명은 설치기가 바뀌면 낡는다.
log "② 이 계획이 실제로 무엇을 할지 (위 출력에서 그대로 집계했다)"
awk '
  /^[0-9]+\. / { kind[$2]++; total++ }
  $2 == "check" { checks = checks (checks ? ", " : "") $3 }
  END {
    for (k in kind) printf("    %-18s %d\n", k, kind[k]) | "sort -k2 -nr"
    close("sort -k2 -nr")
    printf("    %-18s %d\n", "(합계)", total)
    if (checks != "") printf("    판정(check): %s\n", checks)
  }
' "$DRY_LOG"
cat <<'EOF'
    account/group/directory/file = 만들어지거나 내용이 맞춰진다 (이미 같으면 다음 실행 계획에서 사라진다)
    deploy-key/peer-attest-key   = 키를 생성한다 (개인키는 출력되지 않는다)
    timer                        = systemd 타이머 활성화 — 자동 업데이트가 켜지는 지점
    check                        = 쓰기가 아니라 판정. --dry-run에서는 실행되지 않는다
    각 항목을 읽는 법: docs/guide/install.md §4
EOF
echo
log "계획 전문: $DRY_LOG"

if [[ "$dry_run_only" -eq 1 ]]; then
  log "--dry-run-only 이므로 여기서 끝낸다. 실제 설치는 이 옵션 없이 다시 실행한다."
  exit 0
fi

# --------------------------------------------------- ③ 실제 설치 전 마지막 점검
if [[ -z "${DISCORD_BOT_TOKEN:-}" ]]; then
  warn "DISCORD_BOT_TOKEN이 환경에 없다. discord-readiness 체크는 WARN으로 남고 설치는 계속한다."
  warn "  지금 올려 두면 설치 중에 판정된다: set -a; . ~/.env.secrets; set +a"
fi

sudo_prefix=()
if [[ "$(id -u)" -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 \
    || die "실제 설치는 root 권한이 필요한데 sudo가 없다. root로 다시 실행한다"
  sudo_prefix=(sudo)
  if [[ -n "${DISCORD_BOT_TOKEN:-}" ]]; then
    # sudo는 기본적으로 환경을 지운다. 토큰은 argv에 올리지 않는다(ps로 보인다).
    if sudo --preserve-env=DISCORD_BOT_TOKEN true >/dev/null 2>&1; then
      sudo_prefix=(sudo --preserve-env=DISCORD_BOT_TOKEN)
    else
      warn "sudoers가 환경 보존을 거부했다. DISCORD_BOT_TOKEN이 설치기에 전달되지 않아"
      warn "  discord-readiness 체크가 WARN으로 남는다. root 셸에서 직접 실행하면 판정까지 된다."
    fi
  fi
fi

if [[ "$assume_yes" -eq 0 ]]; then
  # `[[ -r /dev/tty ]]`로는 부족하다 — 컨테이너에서는 파일은 있고 열기만 실패해,
  # 안내 대신 빈 입력으로 취소된다. 서브셸에서 실제로 열어본다(실패가 부모를 죽이지 않는다).
  (: < /dev/tty) 2>/dev/null \
    || die "확인을 받을 터미널이 없다. 비대화 실행이라면 --yes를 명시한다" 2
  printf '[quickstart] 위 계획대로 실제 설치를 진행한다. 진행하려면 yes 를 입력한다: '
  reply=""
  read -r reply < /dev/tty || true
  echo
  if [[ "$reply" != "yes" ]]; then
    log "취소했다. 아무것도 실행하지 않았다."
    exit 3
  fi
fi

log "③ 실제 설치 — 멱등하다. 막히면 원인을 고치고 같은 명령을 다시 실행하면 된다."
echo
set +e
"${sudo_prefix[@]}" python3 -m automation.install "${installer_args[@]}" 2>&1 | tee "$RUN_LOG"
run_rc=${PIPESTATUS[0]}
set -e
echo

# ------------------------------------------------ ④ 종료 게이트 판정 요약(설치기 출력)
log "④ 종료 게이트 (설치기가 낸 판정을 그대로 읽었다)"
health_line="$(grep -E '^\[(PASS|WARN|FAIL)\] healthcheck: ' "$RUN_LOG" | tail -n 1 || true)"
trust_lines="$(grep -E '^\[(PASS|WARN|FAIL)\] trust-key\.' "$RUN_LOG" || true)"
verdict_line="$(grep -E '^--- (INSTALLED|NOT-INSTALLED):' "$RUN_LOG" | tail -n 1 || true)"

if [[ -n "$trust_lines" ]]; then
  printf '%s\n' "$trust_lines" | sed 's/^/    /'
fi
if [[ -n "$health_line" ]]; then
  printf '    %s\n' "$health_line"
  case "$health_line" in
    '[PASS]'*) log "    healthcheck: PASS — healthcheck.sh가 ALL_HEALTHY다" ;;
    *)         log "    healthcheck: FAIL — 실패한 프로브 이름은 healthcheck 로그가 지목한다" ;;
  esac
else
  log "    healthcheck: 도달하지 못했다 — 앞 단계에서 먼저 멈췄다. 멈춘 지점:"
  grep -E '^(INSTALL-BLOCK|USAGE-ERROR|\[FAIL\])' "$RUN_LOG" | sed 's/^/      /' || true
fi
[[ -n "$verdict_line" ]] && printf '    %s\n' "$verdict_line"
echo

if [[ "$run_rc" -eq 0 ]]; then
  log "설치 완료 (rc=0). 다음: docs/guide/quickstart-install.md 의 '설치 다음' 절"
  # 설치기는 인프라만 판정한다. Codex·Discord·Google 승인은 사람 몫이라 doctor 가 절차와 함께 보여 준다.
  # 결과는 안내일 뿐 설치 종료코드를 바꾸지 않는다.
  log "⑤ 연결·승인 점검 — python3 -m automation.doctor (다시 확인할 때도 같은 명령)"
  python3 -m automation.doctor 2>&1 | tee "$log_dir/03-doctor.log" || true
else
  log "설치 미완 (rc=$run_rc). 설치기는 첫 FAIL에서 멈춘다 — 의도된 동작이다."
  log "  위에서 지목된 항목을 고치고 **같은 명령을 그대로 다시 실행**한다(멱등)."
  log "  자주 막히는 곳: docs/guide/install.md §8"
fi
log "전문: $RUN_LOG"
exit "$run_rc"

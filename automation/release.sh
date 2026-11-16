#!/usr/bin/env bash
# automation/release.sh — 소유자가 릴리스를 시킨 디스코드 글을 근거로 서명 태그를 자르고 배포한다 (VA-1).
#
# 머지는 축적이고 배포는 릴리스다(§10-1). 이 명령은 clean HEAD == origin/main 에서
# 직전 릴리스 태그..HEAD 의 표면별 변경을 계획하고(release_plan — RC-3 과 같은 선언
# 파서 재사용, 사본 0), 승인 카드 대신 형이 릴리스를 시킨 글(--owner-command <디스코드 링크>)로
# 승인 레코드를 쓴다(형 2026-11-02 21:35 KST 음성, Q447 — 릴리스는 형 ✅ 카드 없이 형 명령으로).
#   노드 agent 계정이 그 글을 확인한다: 형 본인·봇 아님, 릴리스 서버, 24시간 안, 릴리스·배포를
#   말함(음성이면 --owner-inbox 수신기 전사로), 다른 HEAD 에 쓴 적 없음. 아니면 태그 없음, exit 4.
#   형이 도리 질문 카드에서 누른 버튼 답도 같다(형 2026-11-12): 링크가 그 카드면 봇 자신의 글·버튼으로
#   접힌 답·질문과 답 모두 릴리스 낱말·답에 미룸 말 없음을 확인한다(automation/release_command.py).
#   통과 → 기존 release_tag_lib 로 서명 태그 컷(리컨실러가 ~2분 내 수렴) → 같은 단계에서
#        GitHub Release 노트 게시(ensure_release_note). 릴리스 = 태그 + 노트다.
#   형 명령 링크가 없으면 새 릴리스는 시작하지 않는다(exit 4). 이미 기록된 같은 HEAD 의 승인은
#   재실행이 그대로 재개한다(곧장 태그로).
#
# 워크스테이션 전용이다: 서명키가 여기에만 있고(「릴리스 태그 규칙」), CI·노드로 옮기면
# MD-1 이 막은 escalation 이 부활한다. 태그 이후 release.sh 자체가 리컨실러(①)의
# 수렴을 기다려 `automation/deploy_all.sh --apply --wait-converge`(②~⑦ + 영수증)를
# 호출한다. 필요하면 `--no-deploy`로 태그만 자르고 전량 반영을 따로 재개할 수 있다.
#
# 노트 초안(`--notes-file`, 첫 줄 `# <한 줄 요약>` + 한국어 본문)은 승인 요청 **전에** 받아
# 체크아웃 밖 `~/.hermes/release-notes/<sha>.md` 에 둔다 — 초안이 없으면 승인을 쓰지 않고
# 멈추고(exit 4), ✅ 뒤 완결 타이머도 같은 초안으로 노트를 게시한다.
#
# Exit: 0 태그+노트 · 2 usage · 4 전제 미충족(형 명령 없음·거절 포함) · 10 태그 뒤 전량 반영 미완 · 11 태그 뒤 릴리스 노트 게시 실패 · 1 그 외
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${RELEASE_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

log() { printf '[release] %s\n' "$*" >&2; }
die() { log "RELEASE-BLOCK: $1"; exit "${2:-1}"; }

usage="usage: release.sh --owner-command <discord message link> [--owner-inbox <inbox.jsonl>] [--no-deploy] [--bump {major,minor,patch}] [--notes-file <note.md>] [--skip-rehearsal <reason>]"
no_deploy=0
owner_command=""
owner_inbox="${RELEASE_OWNER_INBOX:-}"
skip_rehearsal=""
bump=patch
notes_file=""
while (( $# )); do
  case "$1" in
    --no-deploy) no_deploy=1; shift ;;
    --owner-command)
      (( $# >= 2 )) && [[ -n "$2" ]] || { echo "$usage" >&2; exit 2; }
      owner_command="$2"; shift 2
      ;;
    --owner-inbox)
      (( $# >= 2 )) && [[ -n "$2" ]] || { echo "$usage" >&2; exit 2; }
      owner_inbox="$2"; shift 2
      ;;
    --notes-file)
      (( $# >= 2 )) || { echo "$usage" >&2; exit 2; }
      notes_file="$2"; shift 2
      ;;
    --bump)
      (( $# >= 2 )) || { echo "$usage" >&2; exit 2; }
      case "$2" in major|minor|patch) bump="$2" ;; *) echo "$usage" >&2; exit 2 ;; esac
      shift 2
      ;;
    --skip-rehearsal)
      (( $# >= 2 )) && [[ -n "${2//[[:space:]]/}" ]] || { echo "$usage" >&2; exit 2; }
      skip_rehearsal="$2"; shift 2
      ;;
    --help|-h) echo "$usage"; exit 0 ;;
    *) echo "$usage" >&2; exit 2 ;;
  esac
done

# Host-only opt-in. Invalid content is a refusal even when resuming an approval.
dori_bot_id="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.hermes_agent_chat_ignore --release-switch)" \
  || die "DORI-SWITCH-REFUSED: invalid release-host switch" 4
dori_args=()
[[ -z "$dori_bot_id" ]] || dori_args+=(--dori-agent-chat "$dori_bot_id")
gateway_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.hermes_gateways --release-switch)" \
  || die "GATEWAY-STOP-REFUSED: invalid release-host switch" 4
gateway_args=()
[[ "$gateway_mode" != stopped ]] || gateway_args+=(--hermes-gateways stopped)
legacy_args=()
owner_timers="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.owner_timers switch)" \
  || die "OWNER-TIMERS-REFUSED: invalid release-host switch" 4
owner_timer_args=()
[[ "$owner_timers" == off ]] || owner_timer_args=(--owner-timers-off "$owner_timers")
for service in report-hub hermes-dashboard; do
  legacy_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.legacy_services switch --name "$service")" \
    || die "LEGACY-SERVICE-REFUSED: invalid release-host switch" 4
  [[ "$legacy_mode" != off ]] || legacy_args+=(--legacy-service-off "$service")
done
peer_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.peer_retired --release-switch)" \
  || die "PEER-RETIRED-REFUSED: invalid release-host switch" 4
peer_args=()
[[ "$peer_mode" != retired ]] || peer_args+=(--peer-retired retired)
product_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.hermes_product --release-switch)" \
  || die "HERMES-PRODUCT-REFUSED: invalid release-host switch" 4
product_args=()
[[ "$product_mode" != retired ]] || product_args+=(--hermes-product retired)
retirement_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.gateway_retirement --release-switch)" \
  || die "GATEWAY-RETIRE-REFUSED: invalid release-host switch" 4
retirement_args=()
[[ "$retirement_mode" != retired ]] || retirement_args+=(--gateway-retirement retired)
chat_channel="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.yoodori_channel --release-switch)" \
  || die "YOODORI-CHANNEL-REFUSED: invalid release-host switch" 4
chat_args=()
[[ -z "$chat_channel" ]] || chat_args+=(--yoodori-channel "$chat_channel")
notice_digest="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.notice_bot_release digest)" \
  || die "NOTICE-BOT-REFUSED: invalid switch or unavailable credential" 4
notice_args=()
fault_channel="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.notice_bot_release fault-channel)" \
  || die "FAULT-CHANNEL-REFUSED: invalid release-host switch" 4
if [[ "$fault_channel" != off ]]; then
  [[ "$notice_digest" != off ]] || die "FAULT-CHANNEL-REFUSED: notice-bot required" 4
  notice_args+=(--fault-channel "$fault_channel")
fi
release_channel="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.notice_bot_release release-channel)" \
  || die "RELEASE-NOTICE-CHANNEL-REFUSED: invalid release-host switch" 4
if [[ "$release_channel" != off ]]; then
  [[ "$notice_digest" != off ]] || die "RELEASE-NOTICE-CHANNEL-REFUSED: notice-bot required" 4
  notice_args+=(--release-notice-channel "$release_channel")
fi
auto_kinds="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.auto_write_release switches)" \
  || die "AUTO-WRITE-REFUSED: invalid release-host switch" 4
auto_args=()
ledger_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.ledger.release switch)" \
  || die "LEDGER-SWITCH-REFUSED: invalid release-host switch" 4
ledger_args=()
[[ "$ledger_mode" != on ]] || ledger_args+=(--ledger on)
mail_html_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.mail_html_reply switch)" \
  || die "MAIL-HTML-REPLY-REFUSED: invalid release-host switch" 4
mail_html_args=()
[[ "$mail_html_mode" != on ]] || mail_html_args+=(--mail-html-reply on)
mail_closing_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.mail_reply_closing switch)" \
  || die "MAIL-REPLY-CLOSING-REFUSED: invalid release-host switch" 4
mail_closing_args=()
[[ "$mail_closing_mode" != omit ]] || mail_closing_args+=(--mail-reply-closing omit)
mail_all_day_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.mail_schedule_all_day switch)" \
  || die "MAIL-SCHEDULE-ALL-DAY-REFUSED: invalid release-host switch" 4
mail_all_day_args=()
[[ "$mail_all_day_mode" != on ]] || mail_all_day_args+=(--mail-schedule-all-day on)
mail_user_folders_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.mail_user_folders switch)" \
  || die "MAIL-USER-FOLDERS-REFUSED: invalid release-host switch" 4
mail_user_folders_args=()
[[ "$mail_user_folders_mode" != on ]] || mail_user_folders_args+=(--mail-user-folders on)
calendar_color="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.calendar_default_color switch)" \
  || die "CALENDAR-COLOR-REFUSED: invalid release-host switch" 4
calendar_color_args=()
[[ "$calendar_color" == off ]] || calendar_color_args+=(--calendar-default-color "$calendar_color")
document_ocr_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.document_ocr switch)" \
  || die "DOCUMENT-OCR-REFUSED: invalid release-host switch" 4
document_ocr_args=()
[[ "$document_ocr_mode" != on ]] || document_ocr_args+=(--document-ocr on)
relay_mode="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.omo_relay_release switch)" \
  || die "OMO-RELAY-REFUSED: invalid release-host switch or node configuration" 4
relay_args=()
[[ "$relay_mode" == off ]] || relay_args=(--omo-relay "$relay_mode")
if [[ "$auto_kinds" != off ]]; then
  IFS=',' read -r -a kinds <<< "$auto_kinds"
  for kind in "${kinds[@]}"; do auto_args+=(--auto-write "$kind"); done
fi
if [[ "$notice_digest" != off ]]; then
  [[ "$gateway_mode" == stopped && -n "$chat_channel" ]] \
    || die "NOTICE-BOT-REFUSED: stopped gateways and yoodori channel required" 4
  notice_args+=(--notice-bot "$notice_digest")
fi

# shellcheck source=automation/release_tag_lib.sh
source "${RELEASE_TAG_LIB:-$SCRIPT_DIR/release_tag_lib.sh}"

if [[ -n "${RELEASE_APPROVAL_CMD:-}" ]]; then
  read -r -a approval <<< "$RELEASE_APPROVAL_CMD"
  plan_approval=("${approval[@]}")
else
  approval=("$SCRIPT_DIR/release_approval_remote.sh")
  # The repository exists only on the workstation.  Discord credentials and
  # gate state are remote, but diff planning must stay beside this checkout.
  plan_approval=(python3 -m automation.release_approval)
fi
local_ci="${RELEASE_LOCAL_CI:-$SCRIPT_DIR/local_ci.sh}"

# 전제 ①: clean tree — 검사·승인받는 것과 태그되는 것이 같은 바이트여야 한다.
dirty="$(git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=no \
  -- . ':(exclude).omo/senpi-task')" || die "cannot read the working tree" 4
[[ -z "$dirty" ]] || die "tracked files are modified — 릴리스는 커밋된 트리에서만 자른다" 4

# 전제 ②: HEAD == origin/main — 리컨실러가 수렴하는 것은 origin/main 뿐이다.
git -C "$REPO_ROOT" fetch --quiet origin main --tags || die "could not fetch origin" 4
head="$(git -C "$REPO_ROOT" rev-parse HEAD)" || die "cannot resolve HEAD" 4
main="$(git -C "$REPO_ROOT" rev-parse origin/main)" || die "cannot resolve origin/main" 4
[[ "$head" == "$main" ]] \
  || die "HEAD ${head:0:12} != origin/main ${main:0:12} — 릴리스는 origin/main 에서만 자른다" 4

# 전제 ③: 로컬 CI 영수증 — 기존 push 게이트의 판정을 그대로 재사용한다.
bash "$local_ci" verify "$head" || die "no valid local CI receipt for ${head:0:12}" 4

# 전제 ⑤: 릴리스 노트. 직전 릴리스에 노트가 없으면 새 릴리스를 열지 않는다 — 노트 없는
# 태그는 조용하다(노드는 태그만 본다). 2026-10-08 v1.16.0·v1.17.0 이 그렇게 남았다.
# HEAD 자신이 이미 태그된 재실행이면 아래 태그 단계가 그 노트를 이어서 게시한다.
released_here="$(released_tag_at "$REPO_ROOT" "$head")"
previous_tag="$(git -C "$REPO_ROOT" ls-remote --tags --refs origin 'refs/tags/v*' 2>/dev/null \
  | awk '{ sub("refs/tags/", "", $2); if ($2 ~ /^v[0-9]+\.[0-9]+\.[0-9]+$/) print $2 }' | sort -V | tail -n 1)"
if [[ -n "$previous_tag" && "$previous_tag" != "$released_here" ]]; then
  note_rc=0
  release_note_present "$REPO_ROOT" "$previous_tag" || note_rc=$?
  case "$note_rc" in
    0) ;;
    1) die "RELEASE-NOTE-MISSING: $previous_tag 에 GitHub Release 노트(또는 update-trust.pub)가 없다 — 먼저 보충한다: automation/release-note.sh $previous_tag --notes-file <note.md>" 4 ;;
    *) die "cannot judge the release note of $previous_tag (gh unavailable?)" 4 ;;
  esac
fi
note_draft="$(release_note_draft_path "$head")"
if [[ -n "$notes_file" ]]; then
  release_note_draft_check "$notes_file" || die "the release note draft is not usable" 4
  mkdir -p -m 700 -- "$(dirname -- "$note_draft")" && cp -- "$notes_file" "$note_draft" \
    || die "cannot store the release note draft at $note_draft" 4
fi
if [[ -z "$released_here" ]] || ! release_note_present "$REPO_ROOT" "$released_here"; then
  release_note_draft_check "$note_draft" \
    || die "no release note draft for ${head:0:12} — --notes-file <note.md> 로 준다(첫 줄 '# <한 줄 요약>' + 한국어 본문: 바뀐 것·사용자에게 보이는 변화·깨지는 변경/운영자 할 일)" 4
fi

helper_drift_probe() { # helper_drift_probe → 노드의 판정을 그대로 돌려준다(주입 이음새 있음)
  if [[ -n "${RELEASE_HELPER_PROBE_CMD:-}" ]]; then
    read -r -a probe_cmd <<< "$RELEASE_HELPER_PROBE_CMD"
    "${probe_cmd[@]}"
    return
  fi
  local node_env host script
  node_env="$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env 2>/dev/null)" || return 70
  eval "$node_env"
  host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
  [[ -n "$host" ]] || return 70
  # shellcheck source=automation/release_helper_probe.sh
  source "$SCRIPT_DIR/release_helper_probe.sh" || return 70
  script="$(release_helper_probe_candidate_script "$REPO_ROOT" "$head")" || return 70
  ssh "$host" "sudo -n -u ${NODE_OPS_ACCOUNT:-ops} bash -s" <<< "$script"
}

# 전제 ④: 노드의 root 수렴 도우미가 이 릴리스와 같은 판인가 — 승인을 쓰기 **전에** 본다.
#
# 비교 대상은 노드가 지금 돌리는 릴리스가 아니라 **자르려는 후보**(HEAD)다(2026-10-16).
# 지금 릴리스와 비교하면, 지난 릴리스가 도우미 사본 파일을 바꾸고 이번 릴리스가 그것을
# 되돌리는 경우 되돌리는 릴리스 자체를 영영 자를 수 없다(v1.40.1→v1.40.2 실측).
#
# 그 도우미는 릴리스 트리 **밖** root 정적 사본이라 머지만으로 갱신되지 않는다(MD-1: 자동
# 갱신은 "PR 병합 = root 임의 코드 실행"이 된다). 낡은 사본은 조용히 실패하는 대신 수렴을
# 거부하는데, 그 거부는 태그를 자른 뒤에야 보인다 — 2026-09-22 실측: 승인·서명·태그까지
# 끝난 v1.10.3 이 `deploy_all` 에서 40분을 기다린 뒤 rc=4 로 죽었고, 진짜 사유
# (`SNAPSHOT-BLOCK`)는 노드 저널에만 있었다. 승인 한 번이 통째로 헛돌았다.
#
# 재실행 경로(이미 ✅ 받은 요청)도 같이 막는다 — 낡은 도우미로는 어차피 반영되지 않고,
# 승인 레코드는 살아 있으므로 프로비저너를 돌린 뒤 다시 실행하면 그 자리에서 재개된다.
# 판정하지 **못한** 것(노드 불통·권한)은 위반이 아니라 미상이라 경고만 하고 진행한다 —
# 여기서 막으면 노드가 잠깐 불통인 동안 릴리스 자체가 불가능해진다.
helper_probe_output=""
helper_probe_rc=0
helper_probe_output="$(helper_drift_probe 2>&1)" || helper_probe_rc=$?
if grep -q 'HELPER-DRIFT:' <<< "$helper_probe_output"; then
  printf '%s\n' "$helper_probe_output" >&2
  if [[ "${RELEASE_ALLOW_HELPER_DRIFT:-0}" == "1" ]]; then
    log "RELEASE_ALLOW_HELPER_DRIFT=1 — 낡은 root 도우미를 알고도 진행한다(샌드박스 전용)"
  else
    die "노드의 root 수렴 도우미가 릴리스와 다르다 — 위 안내의 프로비저너를 먼저 돌린다(승인 전에 멈췄다)" 4
  fi
elif (( helper_probe_rc != 0 )); then
  log "HELPER-DRIFT-UNKNOWN: 노드 도우미를 판정하지 못해 그대로 진행한다 — ${helper_probe_output:0:200}"
fi

# 전제 ⑥(2026-10-19 리드 규칙 — 제거 단계가 ✅ 뒤에야 거절해 소유자가 ✅ 를 세 번 다시 눌렀다):
# 새 카드를 올리기 전에 같은 후보 HEAD 의 노드 리허설(`deploy_all.sh --rehearse`)이 최근에 통과했는가.
# 우회는 이유를 적는 `--skip-rehearsal` 뿐이고, 그 이유는 릴리스 노트에 남는다.
rehearsal_note=""
rehearsal_gate() {
  local record="${DEPLOY_ALL_REHEARSAL_DIR:-$HOME/.hermes/deploy-all/rehearsal}/$head"
  local max="${RELEASE_REHEARSAL_MAX_AGE_SECONDS:-21600}" stamp="" sha="" line="" age
  if [[ -n "$skip_rehearsal" ]]; then
    log "REHEARSAL-SKIPPED: $skip_rehearsal — 릴리스 노트에 표시한다"
    rehearsal_note="리허설 생략: $skip_rehearsal"
    return 0
  fi
  [[ -f "$record" ]] \
    || die "REHEARSAL-MISSING: ${head:0:12} 의 노드 리허설 기록이 없다 — automation/deploy_all.sh --rehearse 를 먼저 돌린다(우회: --skip-rehearsal <이유>)" 4
  { read -r stamp sha; IFS= read -r line; } < "$record"
  [[ "$sha" == "$head" && "$stamp" =~ ^[0-9]+$ ]] \
    || die "REHEARSAL-INVALID: $record 이 ${head:0:12} 의 기록이 아니다 — 리허설을 다시 돌린다" 4
  [[ "$line" == "REHEARSAL-OK head=${head:0:12} "* ]] \
    || die "REHEARSAL-REFUSED: 마지막 리허설이 통과하지 않았다 — ${line:0:200}" 4
  age=$(( $(date +%s) - stamp ))
  (( age >= 0 && age <= max )) \
    || die "REHEARSAL-STALE: 리허설이 ${age}초 전이다(상한 ${max}초) — 다시 돌린다" 4
  log "REHEARSAL-PASSED: $line"
  rehearsal_note="리허설: $line"
}
note_rehearsal() { # 노트 초안의 리허설 줄을 이번 결과 한 줄로 바꾼다
  local draft="$1" tmp
  tmp="$(mktemp "$draft.XXXXXX")" || return 1
  { grep -v -e '^리허설: ' -e '^리허설 생략: ' -- "$draft" 2>/dev/null; printf '\n%s\n' "$rehearsal_note"; } > "$tmp" \
    && mv -f -- "$tmp" "$draft" || { rm -f -- "$tmp"; return 1; }
}

workdir="$(mktemp -d)" || die "mktemp failed" 1
trap 'rm -rf -- "$workdir"' EXIT

poll_decision() { # poll_decision → 전역 rc·레코드 버전으로 소유자 결정을 돌려준다
  "${approval[@]}" decision --head "$head" \
    > "$workdir/decision.stdout" 2> "$workdir/decision.stderr"
  decision_rc=$?
  cat "$workdir/decision.stdout"
  cat "$workdir/decision.stderr" >&2
  decision_version=""
  while IFS= read -r decision_line; do
    case "$decision_line" in
      RELEASE-DECISION:\ *\ version=*)
        candidate_version="${decision_line##* version=}"
        if [[ "$candidate_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
          decision_version="$candidate_version"
        fi
        ;;
    esac
  done < "$workdir/decision.stderr"
}

# 세션이 죽은 뒤의 재실행: 살아 있는 요청의 버전은 새 bump 계산보다 먼저 재사용한다.
poll_decision
if (( decision_rc == 0 || decision_rc == 7 )) && [[ -n "$decision_version" ]]; then
  version="$decision_version"
  log "reusing live approval version $version at ${head:0:12}"
else
  version="$(release_version_for "$REPO_ROOT" "$head" "$bump")" \
    || die "could not derive the next version" 4
fi
base="$(latest_release_base "$REPO_ROOT")"
if [[ -z "$base" ]]; then
  base="$(git -C "$REPO_ROOT" rev-list --max-parents=0 HEAD | tail -n 1)" \
    || die "cannot resolve the first release base" 4
fi

if (( decision_rc != 0 )); then
  [[ -n "$owner_command" ]] \
    || die "RELEASE-NO-OWNER-COMMAND: 형이 릴리스를 시킨 디스코드 글 링크를 --owner-command 로 준다 — 형 명령 없이는 릴리스하지 않는다" 4
  # 소유자 결정을 기다리는 옛 카드(rc 7)는 이미 리허설을 지났다 — 그 밖에는 리허설을 요구한다.
  if (( decision_rc != 7 )); then
    rehearsal_gate
    note_rehearsal "$note_draft" || die "cannot record the rehearsal line in $note_draft" 4
  fi
  # 태그 전 main이 전진한 APPROVED도 여기서 감사 회수한다. 새 요청은 새 ✅가 필요하다.
  # 서명 태그는 ✅ 뒤에만 잘린다 — 태그가 가리키는 레코드는 카드 반응이 지금 어떻든 실행 완료다(v1.46.1 사건).
  "${approval[@]}" retire --head "$base" --tip "$head" --released "$(released_tags_map "$REPO_ROOT")" \
    || die "previous release record cannot be archived safely" 4
  # 헬스체크 허용 목록(강제명령 래퍼)은 릴리스 트리 밖 파일이라 수렴이 올리지 못한다. 이 릴리스가 기대할
  # 지문이 설치본과 다른 노드를 표면으로 묶어 같은 ✅ 가 갱신까지 인가한다(소유자 지시 2026-10-14).
  # 판정하지 못하면 카드에 싣지 않고 진행한다 — 그러면 수렴 뒤에도 설치하지 않고 영수증에 남는다.
  wrapper_args=()
  read -r -a wrapper_plan_cmd <<< "${RELEASE_WRAPPER_PLAN_CMD:-bash $SCRIPT_DIR/healthcheck_wrapper_release.sh plan}"
  if wrapper_plan_out="$("${wrapper_plan_cmd[@]}" "$head")"; then
    while IFS= read -r wrapper_line; do
      [[ "$wrapper_line" =~ ^[a-z0-9][a-z0-9.-]*=[0-9a-f]{64}$ ]] \
        && wrapper_args+=(--healthcheck-wrapper "$wrapper_line")
    done <<< "$wrapper_plan_out"
  else
    log "WRAPPER-PLAN-UNKNOWN: 허용 목록 지문을 판정하지 못해 카드에 갱신을 싣지 않는다 — 수렴 뒤 영수증의 남은 소유자 조치로 남는다"
  fi
  rag_args=()
  read -r -a rag_plan_cmd <<< "${RELEASE_RAG_MCP_PLAN_CMD:-bash $SCRIPT_DIR/rag_mcp_release.sh plan}"
  if rag_digest="$("${rag_plan_cmd[@]}" "$head")"; then
    [[ -z "$rag_digest" ]] || {
      [[ "$rag_digest" =~ ^[0-9a-f]{64}$ ]] || die "RAG-MCP-PLAN: invalid digest" 4
      rag_args+=(--rag-mcp "$rag_digest")
    }
  else
    log "RAG-MCP-PLAN-UNKNOWN: RAG 소스·이미지를 판정하지 못해 카드에 갱신을 싣지 않는다 — 수렴 뒤 영수증의 남은 소유자 조치로 남는다"
  fi
  "${plan_approval[@]}" plan --repo "$REPO_ROOT" --base "$base" --head "$head" \
    --version "$version" --bump "$bump" "${wrapper_args[@]}" "${rag_args[@]}" "${dori_args[@]}" "${gateway_args[@]}" "${peer_args[@]}" "${product_args[@]}" "${retirement_args[@]}" "${chat_args[@]}" "${notice_args[@]}" "${auto_args[@]}" "${ledger_args[@]}" "${relay_args[@]}" "${legacy_args[@]}" "${owner_timer_args[@]}" "${mail_html_args[@]}" "${mail_closing_args[@]}" "${mail_all_day_args[@]}" "${mail_user_folders_args[@]}" "${calendar_color_args[@]}" "${document_ocr_args[@]}" > "$workdir/plan.json" \
    || die "release plan failed" 4
  attach_args=(attach --plan-file "$workdir/plan.json" --url "$owner_command")
  [[ -z "$owner_inbox" ]] || attach_args+=(--inbox "$(realpath -m -- "$owner_inbox")")
  # cd: `python3 -m` puts the cwd first on sys.path, so another checkout's older automation
  # package would shadow this one (the same trap release_complete.sh guards against).
  (cd "$SCRIPT_DIR/.." && PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.release_command "${attach_args[@]}") \
    || die "RELEASE-COMMAND-REFUSED: 형 명령 링크를 계획에 싣지 못했다" 4
  command_rc=0
  "${approval[@]}" command --plan-file "$workdir/plan.json" \
    > "$workdir/record.json" 2> "$workdir/command.stderr" || command_rc=$?
  cat "$workdir/command.stderr" >&2
  (( command_rc == 0 )) \
    || die "RELEASE-COMMAND-REFUSED: 형 명령 글로 승인 레코드를 쓰지 못했다(rc=$command_rc) — 태그 없음, prod 불변" 4
  poll_decision
  (( decision_rc == 0 )) \
    || die "RELEASE-COMMAND-UNVERIFIED: 형 명령 레코드를 다시 확인하지 못했다(rc=$decision_rc) — 태그 없음" 1
  log "owner command accepted: $version at ${head:0:12} — $owner_command"
else
  log "approved release request already live for ${head:0:12} — resuming to the tag cut"
fi

ensure_signed_tag "$REPO_ROOT" "$head" "$version" || die "signed release tag failed" 1
ensure_release_note "$REPO_ROOT" "$version" "$head" \
  || die "RELEASE-NOTE-FAIL: $version 의 태그는 잘렸지만 GitHub Release 노트가 없다 — 릴리스는 끝나지 않았다; 재실행(automation/release.sh)이 노트를 이어서 게시한다" 11
log "released $version at ${head:0:12} with its release note — 리컨실러가 ~2분 내 수렴한다"
if (( no_deploy )); then
  log "수렴 후 전량 반영·영수증: automation/deploy_all.sh --apply"
  exit 0
fi
# 태그는 잘렸다 — 여기서부터의 실패는 태그를 되돌리지 않는다. 노드는 리컨실러로 수렴하고,
# 전량 반영은 재실행(release.sh 또는 deploy_all.sh --apply)이 이어받는다.
"${RELEASE_DEPLOY_ALL:-$SCRIPT_DIR/deploy_all.sh}" --apply --wait-converge
rc=$?
(( rc == 0 )) \
  || die "full deployment did not complete (deploy_all rc=$rc) — 태그는 잘렸고 릴리스 트리는 수렴한다; 재실행이 재개다 (automation/release.sh 또는 automation/deploy_all.sh --apply)" 10
log "fully deployed $version at ${head:0:12} — receipt written by deploy_all"

#!/usr/bin/env bash
# automation/release.sh — 릴리스 버전 하나를 소유자 ✅ 로 인가받아 서명 태그를 자른다 (VA-1).
#
# 머지는 축적이고 배포는 릴리스다(§10-1). 이 명령은 clean HEAD == origin/main 에서
# 직전 릴리스 태그..HEAD 의 표면별 변경을 계획하고(release_plan — RC-3 과 같은 선언
# 파서 재사용, 사본 0), 승인 요청을 게시한 뒤 소유자 결정을 계속 기다린다.
#   ✅ → 기존 release_tag_lib 로 서명 태그 컷(리컨실러가 ~2분 내 수렴) → 같은 단계에서
#        GitHub Release 노트 게시(ensure_release_note). 릴리스 = 태그 + 노트다.
#   ⛔ → 태그 없음, prod 불변, exit 9
#   기본 무응답 → 계속 대기. RELEASE_DEADLINE_SECONDS 를 명시한 운영만 exit 8.
#   프로세스가 죽어도 재실행은 살아 있는 요청을 재사용하고 ✅ 뒤 곧장 태그로 간다.
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
# Exit: 0 태그+노트 · 2 usage · 4 전제 미충족 · 8 결정 대기 초과 · 9 소유자 취소 · 10 태그 뒤 전량 반영 미완 · 11 태그 뒤 릴리스 노트 게시 실패 · 1 그 외
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${RELEASE_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

log() { printf '[release] %s\n' "$*" >&2; }
die() { log "RELEASE-BLOCK: $1"; exit "${2:-1}"; }

usage="usage: release.sh [--no-deploy] [--bump {major,minor,patch}] [--notes-file <note.md>]"
no_deploy=0
bump=patch
notes_file=""
while (( $# )); do
  case "$1" in
    --no-deploy) no_deploy=1; shift ;;
    --notes-file)
      (( $# >= 2 )) || { echo "$usage" >&2; exit 2; }
      notes_file="$2"; shift 2
      ;;
    --bump)
      (( $# >= 2 )) || { echo "$usage" >&2; exit 2; }
      case "$2" in major|minor|patch) bump="$2" ;; *) echo "$usage" >&2; exit 2 ;; esac
      shift 2
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
auto_kinds="$(PYTHONPATH="$SCRIPT_DIR/.." python3 -B -m automation.auto_write_release switches)" \
  || die "AUTO-WRITE-REFUSED: invalid release-host switch" 4
auto_args=()
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

post_request() { # post_request → 요청을 게시한다. stderr 는 붙잡되 언제나 그대로 되울린다.
  "${approval[@]}" request --plan-file "$workdir/plan.json" \
    > "$workdir/record.json" 2> "$workdir/request.stderr"
  local rc=$?
  cat "$workdir/request.stderr" >&2   # 소유자 안내 줄은 성공·실패 모두 터미널에 닿아야 한다
  return "$rc"
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
    --version "$version" --bump "$bump" "${wrapper_args[@]}" "${rag_args[@]}" "${dori_args[@]}" "${gateway_args[@]}" "${chat_args[@]}" "${notice_args[@]}" "${auto_args[@]}" > "$workdir/plan.json" \
    || die "release plan failed" 4
  request_refusal="approval request was refused — 살아 있는 다른 요청은 파괴하지 않는다 (⛔ 로 막혔다면 노드 agent 계정에서: python3 -m automation.release_abandon --version <v> --head <sha> --message-id <id> --reason <why>)"
  if ! post_request; then
    # 낡은 pending 요청의 자동 복구. 두 세션이 번갈아 릴리스하면, 소유자 결정을 아직
    # 기다리는(bound_pending) 요청이 이미 지나간 커밋에 묶인 채 남아 이후 모든 요청을
    # binding-mismatch 로 죽인다(2026-08-31 v1.0.141@6a03321f 실측 — 사람이 노드에서
    # release_abandon 을 돌려야 풀렸다).
    #
    # 후속 과제 항목은 staleness 를 'head 가 origin/main 의 조상이 아님'으로 적었지만,
    # 사건의 head 6a03321f 는 origin/main 의 조상이었다. 다시 릴리스될 수 있는 것은
    # 지금의 origin/main tip 하나뿐이므로(전제 ② + main 은 ff-only), tip 불일치가
    # 충실한 조건이다 — 조상 여부는 낡은 요청을 낡지 않았다고 잘못 말한다.
    stale_version=""; stale_head=""; stale_message_id=""; stale_probe=""
    if grep -q 'reason=binding-mismatch' "$workdir/request.stderr"; then
      stale_line="$(grep -m1 '^RELEASE-REQUEST-STALE: ' "$workdir/request.stderr" || true)"
      if [[ -n "$stale_line" ]]; then
        read -r -a stale_fields <<< "${stale_line#RELEASE-REQUEST-STALE: }"
        for stale_field in "${stale_fields[@]}"; do
          case "$stale_field" in
            version=*) stale_version="${stale_field#version=}" ;;
            head=*) stale_head="${stale_field#head=}" ;;
            message_id=*) stale_message_id="${stale_field#message_id=}" ;;
            probe=*) stale_probe="${stale_field#probe=}" ;;
          esac
        done
      fi
    fi
    recovered=0
    if [[ "$stale_probe" == "bound_pending" && -n "$stale_version" && -n "$stale_head" \
          && -n "$stale_message_id" && "$stale_head" != "$main" ]]; then
      if "${approval[@]}" abandon --version "$stale_version" --head "$stale_head" \
        --message-id "$stale_message_id" \
        --reason "stale pending release superseded by origin/main advance (release.sh auto-recovery)"; then
        log "abandoned stale pending release $stale_version at ${stale_head:0:12} — 요청을 한 번만 다시 게시한다"
        post_request && recovered=1   # 재시도는 정확히 한 번이다 — 루프는 없다
      fi
    fi
    (( recovered == 1 )) || die "$request_refusal" 1
  fi
  log "approval requested: $version at ${head:0:12}"

  deadline_seconds="${RELEASE_DEADLINE_SECONDS:-}"
  [[ -z "$deadline_seconds" || "$deadline_seconds" =~ ^[0-9]+$ ]] \
    || die "RELEASE_DEADLINE_SECONDS must be an unsigned integer" 2
  deadline=0
  [[ -z "$deadline_seconds" ]] || deadline=$(( SECONDS + deadline_seconds ))
  transient_failures=0
  while :; do
    poll_decision
    case "$decision_rc" in
      0) break ;;
      9) die "owner cancelled $version — 태그 없음, prod 불변" 9 ;;
      7) transient_failures=0 ;;
      2) die "release request disappeared or was rebound (rc=2)" 1 ;;
      *)
        # rc=255 등은 SSH 불통 같은 일시 실패일 수 있다 — 한 번에 죽지 않고
        # 연속 10회까지 재시도한다(2026-08-31 실측: 일시 255 가 대기를 죽였다).
        transient_failures=$(( transient_failures + 1 ))
        (( transient_failures < 10 )) \
          || die "release request is no longer decidable (rc=$decision_rc, ${transient_failures}회 연속)" 1
        log "decision poll failed (rc=$decision_rc); retrying (${transient_failures}/10)"
        ;;
    esac
    if [[ -n "$deadline_seconds" ]] && (( SECONDS >= deadline )); then
      log "no owner decision within the window — 요청은 살아 있고, 재실행이 곧 재개다"
      exit 8
    fi
    sleep "${RELEASE_POLL_SECONDS:-15}"
  done
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

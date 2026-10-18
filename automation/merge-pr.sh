#!/usr/bin/env bash
# PR 을 머지하는 유일한 경로 — 체크가 green 일 때만 머지한다. 머지는 축적이고 배포는 릴리스다.
#
# 왜 필요한가: 이 저장소는 브랜치 보호를 쓸 수 없다(private + Free 는 403). 켤 수 있다 해도
# 에이전트 토큰이 `admin` 이라 기본 설정은 그를 통과시키고, 관리자까지 묶으면
# `automation/land.sh` 의 main 직접 착지가 서버에서 거부된다. 그래서 판정을 서버가 아니라
# 머지 **명령 자체**에 둔다. 2026-08-25 에 두 번 뚫렸다 — PR #267 은 빨간 CI 위에서,
# PR #269 는 체크가 큐잉되기도 전에(`no checks reported`) 머지됐다.
#
# 왜 태그를 자르지 않는가(VA-3, §10-1): 머지=축적, 릴리스=배포. 서명 태그는 소유자 ✅ 1회를
# 받는 `automation/release.sh` 가 자른다(VA-1) — 머지마다 태그를 자르면 그 릴리스 승인이
# 있으나 마나가 된다. 태그 없는 창의 리컨실러는 sha별 사고 통지 대신 3일 임계의 릴리스
# 백로그 다이제스트를 보낸다(deploy_reconcile.reconcile_unsigned_head).
#
# PR 체크는 로컬 영수증이 덮지 못하는 것을 덮는다: GitHub 의 `pull_request` 실행은 **머지 결과
# 트리**를 검사하고, `automation/local_ci.sh` 의 영수증은 브랜치 트리만 검사한다. 2026-08-25
# 에 실제로 갈렸다(머지 결과 43c50a44 vs 영수증 57081383).
#
# 워크플로 잡과 외부 앱 체크는 다르게 기다린다(2026-10-01). 워크플로가 선언한 잡은 전부 끝나고
# 성공해야 한다. 그 밖의 체크(예: GitGuardian)도 실패하면 언제나 거부하지만, 워크플로 잡이 다
# green 인데 외부 체크만 유예 뒤에도 끝나지 않으면 PR diff 를 `gitleaks stdin` 으로 직접 스캔해
# 깨끗할 때만 머지한다. 2026-09-30 PR #557 에서 GitGuardian 이 평소 1초 걸리던 판정을 20분 넘게
# `in_progress` 로 붙잡았고(재실행 API 는 404), 탈출구 말고는 머지할 길이 없었다. gitleaks 가
# 없거나 diff 를 읽지 못하거나 무엇이든 찾으면 거부한다.
#
# 사용:
#   automation/merge-pr.sh <pr-number>
#
# Env:
#   MERGE_PR_ALLOW_UNCHECKED   체크가 **아예 나오지 않을 때만**의 탈출구(예: Actions 결제 중단).
#                              실패한 체크는 이것으로도 통과하지 못한다. 쓰면 stderr 에 남는다.
#   MERGE_PR_GH                default gh (테스트 주입용 이음새)
#   MERGE_PR_POLL_SECONDS                 default 15
#   MERGE_PR_DEADLINE_SECONDS             default 900
#   MERGE_PR_MERGEABILITY_RETRIES         default 3
#   MERGE_PR_MERGEABILITY_POLL_SECONDS    default 2
#   MERGE_PR_EXTERNAL_GRACE_SECONDS       default 180 — 워크플로 잡이 green 인 뒤 외부 체크를 기다리는 시간
#   MERGE_PR_GITLEAKS                     default gitleaks (테스트 주입용 이음새)
set -uo pipefail

log() { printf '[merge-pr] %s\n' "$*"; }
die() { printf '[merge-pr] %s\n' "$1" >&2; exit "${2:-1}"; }

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || die "not inside a git checkout — run this from the repository"
readonly REPO_ROOT
readonly GH="${MERGE_PR_GH:-gh}"
readonly POLL_SECONDS="${MERGE_PR_POLL_SECONDS:-15}"
readonly DEADLINE_SECONDS="${MERGE_PR_DEADLINE_SECONDS:-900}"
readonly MERGEABILITY_RETRIES="${MERGE_PR_MERGEABILITY_RETRIES:-3}"
readonly MERGEABILITY_POLL_SECONDS="${MERGE_PR_MERGEABILITY_POLL_SECONDS:-2}"
readonly EXTERNAL_GRACE_SECONDS="${MERGE_PR_EXTERNAL_GRACE_SECONDS:-180}"
readonly GITLEAKS="${MERGE_PR_GITLEAKS:-gitleaks}"

pr="${1:-}"
[[ "$pr" =~ ^[0-9]+$ ]] || die "usage: merge-pr.sh <pr-number>" 2

#: 한 줄 판정으로 접는다. 체크가 0건인 상태는 PENDING 이지 GREEN 이 아니다 — 이름 없는 성공은 없다.
read -r -d '' JUDGE_PY <<'PY' || true
import json
import sys

PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}

payload = json.loads(sys.argv[1])
required = {name for name in sys.argv[2].split(",") if name}
checks = payload.get("statusCheckRollup") or []
mergeable = str(payload.get("mergeable") or "").upper()
missing = sorted(required - {str(one.get("name")) for one in checks})
unsettled = [one for one in checks if one.get("status") != "COMPLETED"]
waiting = [one for one in unsettled if str(one.get("name")) in required]
external = [one for one in unsettled if str(one.get("name")) not in required]
failing = [
    one for one in checks
    if str(one.get("conclusion") or "").upper() not in PASSING
    and one.get("status") == "COMPLETED"
]


def names(rows):
    return ", ".join(str(one.get("name")) for one in rows)


if payload.get("state") != "OPEN":
    print(f"STATE {payload.get('state')}")
elif payload.get("baseRefName") != "main":
    print(f"BASE {payload.get('baseRefName')}")
elif mergeable == "UNKNOWN":
    print("MERGEABILITY-UNKNOWN")
elif mergeable == "CONFLICTING":
    print("CONFLICT")
elif failing:
    print("RED " + names(failing))
elif missing:
    print("PENDING not reported yet: " + ", ".join(missing))
elif waiting:
    print("PENDING " + names(waiting))
elif external:
    print("EXTERNAL " + names(external))
else:
    print(f"GREEN {len(checks)} check(s)")
PY

#: 기다려야 할 잡 이름은 워크플로에서 파생한다. 하드코딩하면 잡이 늘 때 조용히 새고, 목록을
#: 안 쓰면 "그 순간 올라와 있던 체크"만 보게 된다 — 2026-08-26 에 체크 1건으로 통과했다.
required_jobs="$(python3 -c '
import sys
from pathlib import Path

import yaml

print(",".join(yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))["jobs"]))
' "$REPO_ROOT/.github/workflows/ci.yml" 2>/dev/null)" \
  || die "cannot read the job list from .github/workflows/ci.yml — refusing to judge"
[[ -n "$required_jobs" ]] || die "the workflow declares no job — refusing to judge"

#: 외부 체크가 끝나지 않을 때 그 자리를 대신하는 비밀 스캔 — 워크플로 잡은 이미 전부 green 이다.
#: 판정 문장만 돌려주고 비밀 값은 싣지 않는다(--redact, 출력 버림).
compensate_external() {
  local diff_file rc config=()
  command -v "$GITLEAKS" >/dev/null 2>&1 \
    || { printf 'gitleaks is not available (%s)' "$GITLEAKS"; return 1; }
  diff_file="$(mktemp)" || { printf 'cannot create a temporary file'; return 1; }
  if ! "$GH" pr diff "$pr" > "$diff_file" 2>/dev/null || [[ ! -s "$diff_file" ]]; then
    rm -f -- "$diff_file"
    printf 'cannot read the diff of pull request %s' "$pr"
    return 1
  fi
  [[ -f "$REPO_ROOT/.gitleaks.toml" ]] && config=(--config "$REPO_ROOT/.gitleaks.toml")
  "$GITLEAKS" stdin --no-banner --redact --log-level error --exit-code 3 "${config[@]}" \
    < "$diff_file" > /dev/null 2>&1
  rc=$?
  rm -f -- "$diff_file"
  case "$rc" in
    0) printf 'gitleaks found no secret in the diff of pull request %s' "$pr" ;;
    3) printf 'gitleaks found a secret in the diff of pull request %s' "$pr"; return 1 ;;
    *) printf 'gitleaks failed (rc=%s)' "$rc"; return 1 ;;
  esac
}

deadline=$(( SECONDS + DEADLINE_SECONDS ))
mergeability_unknown_attempts=0
external_since=""
while :; do
  view="$("$GH" pr view "$pr" \
    --json state,baseRefName,headRefOid,mergeable,statusCheckRollup 2>/dev/null)" \
    || die "cannot read pull request $pr"
  reading="$(python3 -c "$JUDGE_PY" "$view" "$required_jobs")" \
    || die "cannot judge the checks of pull request $pr"
  [[ "$reading" == EXTERNAL* ]] || external_since=""

  case "$reading" in
    GREEN*)
      log "checks ${reading#GREEN } passed"
      break
      ;;
    RED*)
      die "REFUSED — failing check(s): ${reading#RED }. Fix them; the escape hatch does not cover a failure."
      ;;
    STATE*)
      die "REFUSED — pull request $pr is ${reading#STATE }, not OPEN."
      ;;
    BASE*)
      die "REFUSED — pull request $pr targets ${reading#BASE }, not main."
      ;;
    MERGEABILITY-UNKNOWN)
      if (( mergeability_unknown_attempts < MERGEABILITY_RETRIES )); then
        (( mergeability_unknown_attempts += 1 ))
        sleep "$MERGEABILITY_POLL_SECONDS"
        continue
      fi
      die "MERGEABILITY-UNKNOWN — GitHub is still computing whether pull request $pr can merge cleanly. Re-run automation/merge-pr.sh $pr." 4
      ;;
    CONFLICT*)
      die "REFUSED — pull request $pr cannot be merged cleanly. Merge origin/main into the branch, re-run automation/local_ci.sh run, then push."
      ;;
    EXTERNAL*)
      [[ -n "$external_since" ]] || external_since=$SECONDS
      if [[ "${MERGE_PR_ALLOW_UNCHECKED:-0}" != "1" ]] \
        && (( SECONDS - external_since >= EXTERNAL_GRACE_SECONDS )); then
        verdict="$(compensate_external)" \
          || die "REFUSED — external check(s) ${reading#EXTERNAL } did not settle within ${EXTERNAL_GRACE_SECONDS}s and the compensating scan did not clear the diff: ${verdict}."
        printf '[merge-pr] EXTERNAL-UNSETTLED %s after %ss — %s.\n' \
          "${reading#EXTERNAL }" "$EXTERNAL_GRACE_SECONDS" "$verdict" >&2
        break
      fi
      ;;
  esac

  unsettled="${reading#PENDING }"
  unsettled="${unsettled#EXTERNAL }"
  if [[ "${MERGE_PR_ALLOW_UNCHECKED:-0}" == "1" ]]; then
    printf '[merge-pr] MERGE_PR_ALLOW_UNCHECKED=1 — merging %s with unsettled checks (%s).\n' \
      "$pr" "$unsettled" >&2
    break
  fi
  (( SECONDS < deadline )) \
    || die "REFUSED — checks did not settle within ${DEADLINE_SECONDS}s (${unsettled})"
  sleep "$POLL_SECONDS"
done

# Root-helper sources are installed on the node as root-owned copies; a merge alone does not
# update them and the next release.sh stops with HELPER-DRIFT. Say so in the PR body first.
if changed="$("$GH" pr diff "$pr" --name-only 2>/dev/null)"; then
  helper_hits="$(printf '%s\n' "$changed" | (cd "$REPO_ROOT" && python3 -B -m automation.root_helper_sources "$REPO_ROOT"))" \
    || die "cannot judge root-helper sources"
  if [[ -n "$helper_hits" ]]; then
    body="$("$GH" pr view "$pr" --json body --jq .body 2>/dev/null)" || die "cannot read the body of pull request $pr"
    grep -q "ROOT-HELPER-BLOCK" <<<"$body" || die "REFUSED — pull request $pr changes files the node keeps as root-owned copies:
$helper_hits
The next release.sh stops with HELPER-DRIFT until the owner reruns that provisioner as root. Add a line 'ROOT-HELPER-BLOCK: <the owner root step>' to the PR body, or keep the change out of these files."
  fi
else
  log "ROOT-HELPER-UNKNOWN: cannot list the changed files; release.sh still checks the node helper before any card"
fi

log "merging pull request $pr"
"$GH" pr merge "$pr" --merge \
  || die "merge failed"

log "landed pull request $pr — 머지는 축적이다. 배포하려면: automation/release.sh (VA-1)"

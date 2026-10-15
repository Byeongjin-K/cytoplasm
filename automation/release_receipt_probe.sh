#!/usr/bin/env bash
# 릴리스 영수증 프로브 — "현재 릴리스가 전량 반영되었는가"를 추측이 아니라 조회로 만든다 (RC-4).
#
# 영수증(`deploy_all.sh` 가 전량 재판정 통과 시에만 기록)의 release_sha 와 현재 릴리스
# 포인터를 대조한다. 짧은 세대 전환 유예 외에는 영수증 없음·판독 불가·sha 불일치는 FAIL —
# "아마 다 배포됐겠지"가 중복 개발을 부른 그 추측이고, 이 프로브가 그 추측을 없앤다.
#
# 이 영수증은 **롤아웃 증명**이다: 그 릴리스가 한 번 전량 반영되었음을 말한다. 이후의
# 개별 드리프트(단독 배포·손 수정)는 상시 프로브(skill_mounts_current ·
# watcher_wrappers_current 등)가 계속 잡는다 — 역할이 겹치지 않는다(§10-3 조건).
#
# 로컬 프로브다 — cron 호스트(primary node)에서 /srv 를 직접 읽는다(ssh·sudo 없음).
release_receipt_log() { printf '[release-receipt] %s\n' "$*" >&2; }

release_receipt_switch_grace() {
  python3 "$(dirname "${BASH_SOURCE[0]}")/release_switch_grace.py" \
    --current "${HEALTHCHECK_RELEASE_SOURCE_ROOT:-${RUNTIME_RELEASE_CURRENT:-/srv/autophagy-agent-current}}" "$@" >&2
}

probe_release_fully_deployed() { # probe_release_fully_deployed <node> <account> <receipt-path>
  local _node="$1" _account="$2" receipt="$3"
  local current sha recorded fields version

  current="$(readlink "${HEALTHCHECK_RELEASE_SOURCE_ROOT:-${RUNTIME_RELEASE_CURRENT:-/srv/autophagy-agent-current}}" 2>/dev/null)" || {
    release_receipt_log "RECEIPT-UNKNOWN: cannot read the release pointer"
    return 1
  }
  sha="$(basename "$current")"

  if [[ ! -e "$receipt" && ! -L "$receipt" ]] && release_receipt_switch_grace --missing-receipt; then
    return 0
  fi
  if [[ ! -r "$receipt" ]]; then
    release_receipt_log \
      "RECEIPT-MISSING: no full-deployment receipt for release $sha — run automation/deploy_all.sh --verify"
    return 1
  fi

  # 두 줄: release_sha, 그리고 정수 판 번호(정수가 아니거나 없으면 "-").
  fields="$(python3 -I -c '
import json, sys
document = json.load(open(sys.argv[1], encoding="utf-8"))
recorded, version = document["release_sha"], document.get("version")
if not isinstance(recorded, str) or not recorded.strip() or "\n" in recorded:
    sys.exit(1)
print(recorded)
print(version if type(version) is int else "-")
' "$receipt" 2>/dev/null)" || {
    release_receipt_log "RECEIPT-UNKNOWN: unreadable receipt $receipt"
    return 1
  }
  recorded="${fields%%$'\n'*}"
  version="${fields##*$'\n'}"

  if [[ "$recorded" != "$sha" ]]; then
    if [[ "$version" =~ ^[0-9]+$ ]] && (( version >= 2 )) \
      && release_receipt_switch_grace --receipt-sha "$recorded"; then
      return 0
    fi
    release_receipt_log \
      "RECEIPT-STALE: receipt attests $recorded but the release is $sha — run automation/deploy_all.sh --apply"
    return 1
  fi
  # 판 번호 관문은 이 프로브 하나다. v2 미만·판 번호 없음은 "모름"이 아니라 증명 실패다 —
  # 옛 영수증은 위임 프로브와 v2 표면을 판정하지 않았다.
  if [[ ! "$version" =~ ^[0-9]+$ ]] || (( version < 2 )); then
    release_receipt_log \
      "RECEIPT-VERSION: receipt version ${version} cannot attest release $sha — run automation/deploy_all.sh --verify"
    return 1
  fi
  release_receipt_log "RECEIPT-PASS: release $sha fully deployed"
}

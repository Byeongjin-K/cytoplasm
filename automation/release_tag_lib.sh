#!/usr/bin/env bash
# 서명된 릴리스 태그를 자르는 단일 구현.
#
# 2분 리컨실러(`converge_origin_main.sh`)는 **`origin/main` HEAD 자체가 annotated 서명
# 태그의 peel 대상일 때만** 수렴한다. 인자를 받지 않는 것이 그 헬퍼의 계약이라(MD-1),
# 자동 트리거는 설치될 sha 를 고를 수 없고 서명만이 그것을 정한다. 그래서 태그가 없으면
# 프로덕션은 전진하지 않는다 — 조용히. 매 틱 `UPDATE-TRUST-BLOCK` 으로 서지만 rc 0 이다.
#
# 이 구현은 원래 `land.sh` 안에만 있었고, 그래서 **`land.sh` 로 들어온 커밋에만** 태그가
# 붙었다. 브랜치 작업은 land 가 아니라 PR 머지로 main 에 도달하므로(land.sh 헤더의 명시),
# PR 로 들어온 커밋에는 아무도 태그를 붙이지 않았다 — 2026-08-20 실측으로 PR 6건이 그렇게
# 들어갔고 리컨실러가 132회 연속 실패하며 프로덕션이 2커밋 뒤에 얼어 있었다.
#
# 호출자는 `release_tag_log` 를 덮어써 자기 접두사로 로그할 수 있다(land.sh 가 그렇게 한다).

release_tag_log() { printf '[release-tag] %s\n' "$*" >&2; }

#: 서명키는 **로컬에만** 둔다. CI 로 옮기면 "머지 = 프로덕션 임의 코드 실행"이 되어
#: MD-1 이 막으려던 그 escalation 이 그대로 되살아난다(AGENTS.md 「공개 릴리스 규칙」).
: "${UPDATE_TRUST_SIGNING_KEY:=$HOME/.ssh/autophagy_update_trust.pub}"

next_release_tag() { # next_release_tag <repo_root> [major|minor|patch]
  local latest major minor patch bump="${2:-patch}"
  case "$bump" in major|minor|patch) ;; *) return 1 ;; esac
  latest="$(git -C "$1" ls-remote --tags --refs origin 'refs/tags/v*' 2>/dev/null \
    | awk '{ sub("refs/tags/", "", $2); if ($2 ~ /^v[0-9]+\.[0-9]+\.[0-9]+$/) print $2 }' \
    | sort -V | tail -n 1)"
  [[ -n "$latest" ]] || { printf 'v1.0.0\n'; return 0; }
  IFS=. read -r major minor patch <<<"${latest#v}"
  case "$bump" in
    major) printf 'v%s.0.0\n' "$((major + 1))" ;;
    minor) printf 'v%s.%s.0\n' "$major" "$((minor + 1))" ;;
    patch) printf 'v%s.%s.%s\n' "$major" "$minor" "$((patch + 1))" ;;
  esac
}

latest_release_base() { # latest_release_base <repo_root> — the sha the newest release tag peels to
  git -C "$1" ls-remote --tags origin 'refs/tags/v*' 2>/dev/null \
    | awk '$2 ~ /\^\{\}$/ { sub("refs/tags/", "", $2); sub(/\^\{\}$/, "", $2); print $2 " " $1 }' \
    | sort -V | tail -n 1 | awk '{ print $2 }'
}

released_tags_map() { # released_tags_map <repo_root> — v=sha,… for the newest 30 release tags (retire's executed evidence)
  git -C "$1" ls-remote --tags origin 'refs/tags/v*' 2>/dev/null \
    | awk '$2 ~ /^refs\/tags\/v[0-9]+\.[0-9]+\.[0-9]+\^\{\}$/ { sub("refs/tags/", "", $2); sub(/\^\{\}$/, "", $2); print $2 " " $1 }' \
    | sort -V | tail -n 30 | awk '{ printf "%s%s=%s", (NR > 1 ? "," : ""), $1, $2 }'
}

released_tag_at() { # released_tag_at <repo_root> <sha> — the tag already peeling to sha
  git -C "$1" ls-remote --tags origin 2>/dev/null \
    | awk -v sha="$2" '$1 == sha && $2 ~ /\^\{\}$/ { sub("refs/tags/", "", $2); sub(/\^\{\}$/, "", $2); print $2 }' \
    | head -n 1
}

release_version_for() { # release_version_for <repo_root> <sha> [major|minor|patch]
  #: HEAD 에 이미 릴리스 태그가 있으면 그것이 이 릴리스의 버전이다 — 완결기·재실행은 태그 컷
  #: 뒤의 deploy 를 재개하는 것이지 다음 버전을 여는 것이 아니다(2026-09-03: next 를 다시
  #: 계산해 v1.1.2 를 요청하자 ensure_signed_tag 의 이름 불일치 검사가 자기 태그 v1.1.1 을 거부했다).
  local existing
  existing="$(released_tag_at "$1" "$2")"
  if [[ -n "$existing" ]]; then printf '%s\n' "$existing"; return 0; fi
  next_release_tag "$1" "${3:-patch}"
}

ensure_signed_tag() { # ensure_signed_tag <repo_root> <sha> [requested_version]
  local existing tag="${3:-}"
  existing="$(released_tag_at "$1" "$2")"
  if [[ -n "$existing" ]]; then
    if [[ -n "$tag" && "$existing" != "$tag" ]]; then
      release_tag_log "tag at HEAD is $existing, not requested $tag"
      return 1
    fi
    release_tag_log "already released as $existing"
    return 0
  fi
  [[ -f "$UPDATE_TRUST_SIGNING_KEY" ]] \
    || { release_tag_log "no update-trust signing key at $UPDATE_TRUST_SIGNING_KEY"; return 1; }
  if [[ -z "$tag" ]]; then
    tag="$(next_release_tag "$1")" \
      || { release_tag_log "could not read the released tag series"; return 1; }
  fi
  git -C "$1" -c gpg.format=ssh -c "user.signingkey=$UPDATE_TRUST_SIGNING_KEY" \
      tag -s "$tag" -m "release: $tag" "$2" >/dev/null 2>&1 \
    || { release_tag_log "signing tag $tag failed"; return 1; }
  git -C "$1" push origin "$tag" >/dev/null 2>&1 \
    || { release_tag_log "pushing tag $tag failed"; return 1; }
  release_tag_log "signed release tag $tag -> $2"
}

# ── 릴리스 노트 ──────────────────────────────────────────────────────────────
#
# 릴리스 = 서명 태그 + GitHub Release 노트(AGENTS.md 「공개 릴리스 규칙」). 노드의 자동 수렴은
# 태그만 보므로 노트가 빠져도 아무것도 깨지지 않는 것처럼 보인다 — 실제로 v1.16.0·v1.17.0 은
# 태그만 올라가 Latest 가 v1.15.1 에 멈췄고, 이미 배포된 변경이 어떤 노트에도 실리지 않았다.
# 그래서 태그를 자르는 모든 호출부는 ensure_signed_tag 바로 뒤에 ensure_release_note 를 부르고
# (tests/unit/test_release_note_gate.py 가 호출부를 대조한다), 실패는 조용히 넘기지 않는다.
#
# 노트 초안은 사람(세션)이 쓴다: 첫 줄 `# <한 줄 요약>`(Release 제목이 된다) + 한국어 본문.
# 자리는 체크아웃 밖 `${RELEASE_NOTES_DIR:-$HOME/.hermes/release-notes}/<sha>.md` 이고,
# release.sh 가 승인 요청 **전에** 거기 넣어 두므로 ✅ 뒤 완결 타이머도 같은 초안을 쓴다.
# 이 함수는 그 본문 뒤에 머지된 PR 목록과 업데이트 신뢰키 지문을 붙이고 update-trust.pub 를
# 자산으로 올린 뒤, 다시 읽어 본문·자산을 확인한다.

release_note_draft_path() { # release_note_draft_path <sha>
  printf '%s/%s.md\n' "${RELEASE_NOTES_DIR:-$HOME/.hermes/release-notes}" "$1"
}

release_note_draft_check() { # release_note_draft_check <file> — 제목 줄과 비지 않은 본문
  local file="$1" title
  [[ -f "$file" ]] || { release_tag_log "RELEASE-NOTE-FAIL: no note draft at $file"; return 1; }
  title="$(head -n 1 -- "$file")"
  [[ "$title" =~ ^#\ +[^[:space:]] ]] \
    || { release_tag_log "RELEASE-NOTE-FAIL: the first line of $file must be '# <한 줄 요약>'"; return 1; }
  tail -n +2 -- "$file" | grep -q '[^[:space:]]' \
    || { release_tag_log "RELEASE-NOTE-FAIL: $file has a title but no body"; return 1; }
}

release_note_repo() { # release_note_repo <repo_root> — owner/name of the GitHub origin
  if [[ -n "${RELEASE_NOTE_REPO:-}" ]]; then printf '%s\n' "$RELEASE_NOTE_REPO"; return 0; fi
  local url
  url="$(git -C "$1" remote get-url origin 2>/dev/null)" || return 1
  [[ "$url" =~ github\.com[:/]([^/]+/[^/]+)$ ]] || return 1
  printf '%s\n' "${BASH_REMATCH[1]%.git}"
}

release_note_present() { # release_note_present <repo_root> <tag> — rc 0 노트·자산 있음, 1 없음, 2 판정 불가
  local repo json
  repo="$(release_note_repo "$1")" || return 2
  json="$("${RELEASE_GH:-gh}" release view "$2" --repo "$repo" --json body,assets 2>/dev/null)" || return 1
  python3 -c '
import json, sys
data = json.loads(sys.argv[1])
names = {asset.get("name") for asset in data.get("assets") or []}
sys.exit(0 if (data.get("body") or "").strip() and "update-trust.pub" in names else 1)
' "$json"
}

release_note_previous_tag() { # release_note_previous_tag <repo_root> <tag> — 바로 앞 릴리스 태그
  git -C "$1" ls-remote --tags --refs origin 'refs/tags/v*' 2>/dev/null \
    | awk '{ sub("refs/tags/", "", $2); if ($2 ~ /^v[0-9]+\.[0-9]+\.[0-9]+$/) print $2 }' \
    | sort -V | awk -v tag="$2" '$0 == tag { print prev; exit } { prev = $0 }'
}

release_note_compose() { # release_note_compose <repo_root> <tag> <sha> <draft> <pubkey> > body
  local previous range fingerprint merges
  tail -n +2 -- "$4" | sed -e '/./,$!d'
  previous="$(release_note_previous_tag "$1" "$2")"
  range="$3"
  [[ -z "$previous" ]] || range="$(git -C "$1" rev-parse "$previous^{commit}" 2>/dev/null)..$3"
  merges="$(git -C "$1" log --merges --format='%s%x1f%b' "$range" 2>/dev/null \
    | awk -F '\x1f' '/^Merge pull request #[0-9]+/ { split($1, w, " "); title = $2; sub(/\r$/, "", title); print "- " w[4] " " title }')"
  printf '\n## 포함된 PR'
  [[ -z "$previous" ]] || printf ' (%s 이후)' "$previous"
  printf '\n%s\n' "${merges:-- 머지된 PR 없음}"
  fingerprint="$(ssh-keygen -l -f "$5" | awk '{ comment = ""; for (i = 3; i < NF; i++) comment = comment (i > 3 ? " " : "") $i; gsub(/[()]/, "", $NF); print "`" $2 "` (" comment ", " $NF ")" }')" || return 1
  printf '\n## 업데이트 신뢰키\n지문: %s\n첨부된 `update-trust.pub`의 지문을 설치기가 아닌 경로로 이 값과 대조하세요.\n' "$fingerprint"
}

ensure_release_note() { # ensure_release_note <repo_root> <tag> <sha> [draft]
  local root="$1" tag="$2" sha="$3" draft="${4:-}" repo dir pub title latest present_rc=0
  release_note_present "$root" "$tag" || present_rc=$?
  if (( present_rc == 0 )); then
    release_tag_log "release note for $tag already published"
    return 0
  fi
  repo="$(release_note_repo "$root")" \
    || { release_tag_log "RELEASE-NOTE-FAIL: cannot resolve the GitHub repository of $root"; return 1; }
  [[ -n "$draft" ]] || draft="$(release_note_draft_path "$sha")"
  release_note_draft_check "$draft" || return 1
  dir="$(mktemp -d)" || return 1
  pub="$dir/update-trust.pub"
  # 올리는 것은 반드시 공개키다. 키 경로가 개인키면 공개키를 유도한다(개인키가 공개 자산이 되는 덫).
  if head -c 4 -- "$UPDATE_TRUST_SIGNING_KEY" 2>/dev/null | grep -q '^ssh-'; then
    cp -- "$UPDATE_TRUST_SIGNING_KEY" "$pub"
  else
    ssh-keygen -y -f "$UPDATE_TRUST_SIGNING_KEY" > "$pub" 2>/dev/null
  fi
  if ! head -c 4 -- "$pub" 2>/dev/null | grep -q '^ssh-'; then
    rm -rf -- "$dir"
    release_tag_log "RELEASE-NOTE-FAIL: no update-trust public key from $UPDATE_TRUST_SIGNING_KEY"
    return 1
  fi
  if ! release_note_compose "$root" "$tag" "$sha" "$draft" "$pub" > "$dir/body.md"; then
    rm -rf -- "$dir"
    release_tag_log "RELEASE-NOTE-FAIL: could not compose the note for $tag"
    return 1
  fi
  title="$(head -n 1 -- "$draft" | sed -E 's/^#[[:space:]]+//')"
  latest="$(git -C "$root" ls-remote --tags --refs origin 'refs/tags/v*' 2>/dev/null \
    | awk '{ sub("refs/tags/", "", $2); if ($2 ~ /^v[0-9]+\.[0-9]+\.[0-9]+$/) print $2 }' | sort -V | tail -n 1)"
  local gh_rc=0
  if (( present_rc == 1 )) && "${RELEASE_GH:-gh}" release view "$tag" --repo "$repo" >/dev/null 2>&1; then
    # Release 는 있는데 본문이나 자산이 빠졌다 — 고쳐 쓴다(재실행이 재개다).
    "${RELEASE_GH:-gh}" release edit "$tag" --repo "$repo" --title "$tag — $title" \
        --notes-file "$dir/body.md" >/dev/null 2>&1 \
      && "${RELEASE_GH:-gh}" release upload "$tag" "$pub" --repo "$repo" --clobber >/dev/null 2>&1 \
      || gh_rc=$?
  else
    "${RELEASE_GH:-gh}" release create "$tag" "$pub" --repo "$repo" --verify-tag \
        --title "$tag — $title" --notes-file "$dir/body.md" \
        "--latest=$([[ "$tag" == "$latest" ]] && printf true || printf false)" >/dev/null 2>&1 \
      || gh_rc=$?
  fi
  rm -rf -- "$dir"
  (( gh_rc == 0 )) \
    || { release_tag_log "RELEASE-NOTE-FAIL: gh could not publish the release note for $tag (rc=$gh_rc)"; return 1; }
  release_note_present "$root" "$tag" \
    || { release_tag_log "RELEASE-NOTE-FAIL: $tag has no note body or update-trust.pub after publishing"; return 1; }
  release_tag_log "published the release note for $tag"
}

#!/usr/bin/env bash
# automation/deploy_provenance.sh — shared deploy guard: never deploy code that is not in git.
#
# WHY (2026-07-25 선례): every deploy script copies files from the LOCAL checkout to
# prod. With parallel sessions a file is easily deployed while still uncommitted, or
# committed but not pushed. Prod then runs code that does not exist in origin/main —
# and the next deploy from any clean checkout SILENTLY REVERTS it. That is how the
# mail_digest_watch.py DNS-retry fix nearly got lost, and it is the same class as the
# 2026-07-21 incident where an old checkout overwrote a newer session's decision.
#
# The check is a pure content comparison: the working-tree blob hash of each file must
# equal the blob hash of the same path in the deploy reference (default origin/main).
# One comparison therefore catches BOTH "not committed" and "committed but not pushed".
#
# Usage (from a deploy script):
#   source "$repo_root/automation/deploy_provenance.sh"
#   deploy_provenance_check "$repo_root" <file-or-dir>...   # non-zero exit => do not deploy
#
# Env:
#   DEPLOY_PROVENANCE_REF   deploy reference (default: origin/main)
#   DEPLOY_ALLOW_UNPUSHED=1 skip the check — sandbox/testing ONLY, prints a warning.
#                           Never set it to "just get the deploy through": prod would
#                           run code no one else can reproduce or redeploy.
#   DEPLOY_PROVENANCE_GIT_ROOT  git object store used when the source is a sealed release
#                           (default: /srv/autophagy-agents — the node's read-only mirror)

deploy_provenance_log() { printf '[deploy-provenance] %s\n' "$*" >&2; }

# The archive carries ONLY tracked (or not-ignored) files, so `git ls-files` drives tar.
# That listing names files and never directories, which silently dropped the skill ROOT
# entry `<name>/` — and `skill_store.py` requires it, so every privileged mount died with
# "archive lacks the skill root directory" (measured 2026-08-04, stage 4/4). The directory
# arguments are therefore emitted alongside the file list, with `--no-recursion` so tar
# records the entry WITHOUT walking it: an ignored file still cannot enter the archive.
_deploy_archive_dir_entries() { # _deploy_archive_dir_entries <base-dir> <path>...
  local base_dir="$1"; shift
  local path
  for path in "$@"; do
    [[ -d "$base_dir/$path" ]] && printf '%s\0' "$path"
  done
  return 0
}

#: 아카이브에서 뺀 파일 이름을 걸러낸다. 기본은 빈 값 — **지정하지 않은 호출자는 아무것도
#: 달라지지 않는다**(이 헬퍼는 스킬 전용이 아니라 여러 배포 경로가 공유한다).
#: 무엇을 뺀다는 판단은 호출자가 하고, 그 판단은 digest 쪽과 반드시 같아야 한다.
_deploy_archive_filter() { # stdin/stdout: NUL-구분 경로 목록
  local excluded="${DEPLOY_ARCHIVE_EXCLUDE_BASENAMES:-}" entry base
  if [[ -z "$excluded" ]]; then cat; return 0; fi
  while IFS= read -r -d '' entry; do
    base="${entry##*/}"
    case ",$excluded," in *",$base,"*) continue ;; esac
    printf '%s\0' "$entry"
  done
  return 0
}

deploy_archive_stream() { # deploy_archive_stream <repo-root> <base-dir> <path>...
  local repo_root="$1" base_dir="$2"
  shift 2
  [[ $# -gt 0 ]] || { deploy_provenance_log "DEPLOY-BLOCK: no archive paths given"; return 1; }
  local actual_root
  actual_root="$(git -C "$base_dir" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "$actual_root" && "$(readlink -f "$actual_root")" == "$(readlink -f "$repo_root")" ]]; then
    { _deploy_archive_dir_entries "$base_dir" "$@"
      git -C "$base_dir" ls-files -z --cached --others --exclude-standard -- "$@"; } \
      | _deploy_archive_filter \
      | tar -C "$base_dir" --null --verbatim-files-from --no-recursion -czf - --files-from=-
    return ${PIPESTATUS[0]}
  fi
  local temporary_index rc=0
  temporary_index="$(mktemp -d)" || return 1
  git --git-dir="$temporary_index" --work-tree="$base_dir" init -q \
    && git --git-dir="$temporary_index" --work-tree="$base_dir" \
      -c core.excludesFile="$repo_root/.gitignore" add --all -- "$@" \
    && { _deploy_archive_dir_entries "$base_dir" "$@"
         git --git-dir="$temporary_index" --work-tree="$base_dir" ls-files -z -- "$@"; } \
      | _deploy_archive_filter \
      | tar -C "$base_dir" --null --verbatim-files-from --no-recursion -czf - --files-from=- \
    || rc=$?
  rm -rf "$temporary_index"
  return "$rc"
}

# DG-5 moved the node's runtime onto a sealed, read-only release with no `.git`, so the
# autonomous deploy path (the supply-chain watcher) ran the pipeline from a tree this
# guard could not read — it blocked every tick (2026-08-02 실측). The question is
# unchanged ("is every deployed byte in origin/main?"); only the evidence moves, from a
# working tree to the mirror's object store. Verification is STRONGER there: a sealed
# tree is compared in FULL against the commit, so a file git never heard of is caught
# even though no index exists to call it "untracked".
deploy_provenance_release_check() { # deploy_provenance_release_check <release_root>
  local release="$1"
  [[ -f "$release/.origin-sha" ]] || {
    deploy_provenance_log "DEPLOY-BLOCK: $release is neither a git checkout nor a sealed release"
    return 1; }
  local pinned
  # Pin the physical release: `current` is a symlink the reconcile timer may flip mid-deploy,
  # and verifying one release while packaging another would defeat the whole check.
  pinned="$(readlink -f "$release")" || {
    deploy_provenance_log "DEPLOY-BLOCK: cannot resolve $release to a physical release"
    return 1; }
  local verifier="$pinned/automation/release_provenance.py"
  [[ -f "$verifier" ]] || {
    deploy_provenance_log "DEPLOY-BLOCK: release verifier missing: $verifier"
    return 1; }
  python3 "$verifier" --release "$pinned" \
    --git-root "${DEPLOY_PROVENANCE_GIT_ROOT:-/srv/autophagy-agents}" \
    --reference "${DEPLOY_PROVENANCE_REF:-origin/main}"
}

personal_provenance_check() { # personal_provenance_check <personal-repo> [approved-head]
  local repo_root="$1" approved_head="${2:-}"
  [[ -d "$repo_root" ]] || {
    deploy_provenance_log "DEPLOY-BLOCK: personal repository is missing: $repo_root"
    return 1; }

  local actual_root
  actual_root="$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -z "$actual_root" || "$(readlink -f "$actual_root")" != "$(readlink -f "$repo_root")" ]]; then
    deploy_provenance_log "DEPLOY-BLOCK: $repo_root is not its own git repository"
    return 1
  fi

  local branch_ref
  if ! branch_ref="$(git -C "$repo_root" symbolic-ref -q HEAD)"; then
    deploy_provenance_log "DEPLOY-BLOCK: personal repository HEAD is detached"
    return 1
  fi

  local head
  head="$(git -C "$repo_root" rev-parse --verify --quiet 'HEAD^{commit}' 2>/dev/null || true)"
  if [[ -z "$head" ]] || ! git -C "$repo_root" show-ref --verify --quiet "$branch_ref"; then
    deploy_provenance_log "DEPLOY-BLOCK: personal repository HEAD is not a committed branch tip"
    return 1
  fi

  local tracked_dirty
  tracked_dirty="$(git -C "$repo_root" -c core.quotepath=false status --porcelain --untracked-files=no)" || {
    deploy_provenance_log "DEPLOY-BLOCK: cannot inspect personal repository worktree"
    return 1; }
  if [[ -n "$tracked_dirty" ]]; then
    deploy_provenance_log "DEPLOY-BLOCK: personal repository worktree has uncommitted changes"
    while IFS= read -r dirty_path; do deploy_provenance_log "  $dirty_path"; done <<<"$tracked_dirty"
    return 1
  fi

  local untracked
  untracked="$(git -C "$repo_root" -c core.quotepath=false ls-files --others --exclude-standard -- .)" || {
    deploy_provenance_log "DEPLOY-BLOCK: cannot list untracked files in personal repository"
    return 1; }
  if [[ -n "$untracked" ]]; then
    deploy_provenance_log "DEPLOY-BLOCK: personal repository contains untracked files — commit them before deploying"
    while IFS= read -r untracked_path; do deploy_provenance_log "  $untracked_path"; done <<<"$untracked"
    return 1
  fi

  if [[ -n "$approved_head" ]]; then
    if [[ ! "$approved_head" =~ ^[0-9a-f]{40,64}$ || "$head" != "$approved_head" ]]; then
      deploy_provenance_log "DEPLOY-BLOCK: personal repository HEAD differs from the approval record"
      return 1
    fi
  fi

  deploy_provenance_log "OK: personal repository is clean at HEAD $head"
  printf '%s\n' "$head"
  return 0
}

# The shared deploy helpers. Their functions run on the node (`declare -f`) or carry the
# transport, so an uncommitted edit to one is uncommitted code in prod even when every payload
# argument matches (RCB todo 58: a dirty `_converge_cron_remote` installed its own schedule).
# Listed here instead of discovered because most deployers source deploy_cron.sh AFTER the check.
DEPLOY_PROVENANCE_SHARED_HELPERS=(
  automation/deploy_provenance.sh automation/deploy_cron.sh automation/deploy_push.sh
  automation/deploy_tree.sh automation/deploy_tree_remote.sh automation/deploy_config.sh
  automation/deploy_timer.sh
)

# Origins bash reports for code that never came from a file (bash 5.2, measured): `environment`
# for a `bash -c` string or an exported function, `main` for an interactive or stdin shell; an
# empty BASH_SOURCE entry is the top of such a shell. Every other origin must resolve to a file.
_DEPLOY_PROVENANCE_NON_FILE_ORIGINS=" main environment "

# The cwd this shell had when it first sourced the guard — the base bash used for the deployer's
# relative BASH_SOURCE. Captured once per shell (a later re-source, e.g. through deploy_tree.sh,
# keeps it) and never taken from the environment: an inherited value describes another shell.
if [[ "$(declare -p DEPLOY_PROVENANCE_SOURCE_PWD 2>/dev/null)" != "declare -- "* ]]; then
  DEPLOY_PROVENANCE_SOURCE_PWD="$PWD"
  export -n DEPLOY_PROVENANCE_SOURCE_PWD
fi

# Every file inside <repo_root> whose code this deploy runs besides its named payload: the shared
# helpers, each script on the call stack (the deployer itself) and the file defining each function
# already in this shell (helpers sourced before the check). Files outside the checkout — a test
# driver, a shell rc file — are not repository state and are left out. An origin that cannot be
# located fails the whole closure (rc 1): dropping it would pass exactly the file it lost.
_deploy_provenance_closure() { # _deploy_provenance_closure <repo_root> <reference>  -> one path per line
  local root reference="$2" restore name line origin candidate relative listed on_stack failed=0
  local pattern='^[^ ]+ [0-9]+ (.+)$'
  local -a stack=("${BASH_SOURCE[@]}") origins=() found=()
  root="$(readlink -f "$1")" || { deploy_provenance_log "DEPLOY-BLOCK: cannot resolve $1"; return 1; }
  for relative in "${DEPLOY_PROVENANCE_SHARED_HELPERS[@]}"; do
    if [[ -e "$root/$relative" ]]; then found+=("$root/$relative"); continue; fi
    # Absent from the checkout: fine only when the reference never had it (older trees lack some).
    # A tracked helper deleted locally is an uncommitted change — the deployer may source it later.
    listed="$(git -C "$root" ls-tree --name-only "$reference" -- "$relative")" || {
      deploy_provenance_log "DEPLOY-BLOCK: cannot list $relative in $reference"; return 1; }
    if [[ -n "$listed" ]]; then
      deploy_provenance_log "DEPLOY-BLOCK: $relative is in $reference but deleted from the checkout"
      return 1
    fi
  done
  restore="$(builtin shopt -p extdebug)" || true
  if ! shopt -s extdebug || ! builtin shopt -q extdebug; then
    deploy_provenance_log "DEPLOY-BLOCK: cannot enable extdebug to locate the shell's function sources"
    return 1
  fi
  while read -r _ _ name; do
    [[ -n "$name" ]] || continue
    line="$(builtin declare -F "$name")"
    if [[ "$line" =~ $pattern ]]; then
      origins+=("${BASH_REMATCH[1]}")
    else
      deploy_provenance_log "DEPLOY-BLOCK: bash reported no source file for function $name"
      failed=1; break
    fi
  done <<<"$(builtin declare -F)"
  eval "builtin $restore"
  (( failed == 0 )) || return 1
  for origin in "${stack[@]}" "${origins[@]}"; do
    if [[ -z "$origin" || "$_DEPLOY_PROVENANCE_NON_FILE_ORIGINS" == *" $origin "* ]]; then continue; fi
    # bash keeps a relative origin as written, against the cwd of the moment it was sourced, and a
    # later `cd` silently moves that base — any same-name file found now may be a different file.
    # Only a script on the call stack is accepted relatively, and only while the cwd is still the
    # one captured when the guard was sourced; otherwise its identity cannot be established.
    if [[ "$origin" != /* ]]; then
      on_stack=0
      for candidate in "${stack[@]}"; do
        if [[ "$candidate" == "$origin" ]]; then on_stack=1; fi
      done
      if (( on_stack == 0 )); then
        deploy_provenance_log "DEPLOY-BLOCK: a function came from relative $origin, whose directory is unknown"
        return 1
      fi
      if [[ -z "${DEPLOY_PROVENANCE_SOURCE_PWD:-}" || "$PWD" != "$DEPLOY_PROVENANCE_SOURCE_PWD" ]]; then
        deploy_provenance_log "DEPLOY-BLOCK: the cwd changed since the guard was sourced, so relative $origin cannot be placed"
        return 1
      fi
      origin="$DEPLOY_PROVENANCE_SOURCE_PWD/$origin"
    fi
    [[ -f "$origin" ]] && candidate="$(readlink -f "$origin")" || {
      deploy_provenance_log "DEPLOY-BLOCK: code in this shell came from $origin, which is not a readable file"
      return 1; }
    if [[ "$candidate" == "$root"/* ]]; then found+=("$candidate"); fi
  done
  printf '%s\n' "${found[@]}" | sort -u
}

deploy_provenance_check() { # deploy_provenance_check <repo_root> <file-or-dir>...
  local repo_root="$1"
  shift || true
  if [[ "${DEPLOY_ALLOW_UNPUSHED:-}" == "1" ]]; then
    deploy_provenance_log "WARNING: DEPLOY_ALLOW_UNPUSHED=1 — provenance check skipped (sandbox only)"
    return 0
  fi
  [[ $# -gt 0 ]] || { deploy_provenance_log "DEPLOY-BLOCK: no paths given to check"; return 1; }
  if ! git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    deploy_provenance_release_check "$repo_root"
    return $?
  fi

  local reference="${DEPLOY_PROVENANCE_REF:-origin/main}"
  timeout 20 git -C "$repo_root" fetch --quiet "${reference%%/*}" >/dev/null 2>&1 || true
  git -C "$repo_root" rev-parse --verify --quiet "$reference" >/dev/null \
    || { deploy_provenance_log "DEPLOY-BLOCK: deploy reference $reference is unavailable"; return 1; }

  local -a targets=()
  local path
  for path in "$@"; do
    if [[ -d "$path" ]]; then
      local tracked
      tracked="$(git -C "$repo_root" -c core.quotepath=false ls-files -- "$path")" || {
        deploy_provenance_log "DEPLOY-BLOCK: cannot list tracked files under $path"; return 1; }
      [[ -n "$tracked" ]] || { deploy_provenance_log "DEPLOY-BLOCK: $path has no tracked files"; return 1; }
      # Callers ship the WHOLE directory (tar/rsync), not just its tracked files, so
      # a file git never heard of would reach prod having bypassed commit, the deploy
      # reference and every review gate. --exclude-standard is deliberate: gitignored
      # build/runtime residue is declared non-source, and blocking on it would break
      # every real deploy.
      local untracked
      untracked="$(git -C "$repo_root" -c core.quotepath=false ls-files --others --exclude-standard -- "$path")" || {
        deploy_provenance_log "DEPLOY-BLOCK: cannot list untracked files under $path"; return 1; }
      if [[ -n "$untracked" ]]; then
        deploy_provenance_log "DEPLOY-BLOCK: $path contains untracked files — commit and push them before deploying"
        while IFS= read -r untracked_path; do deploy_provenance_log "  $untracked_path"; done <<<"$untracked"
        deploy_provenance_log "  (sandbox testing only: re-run with DEPLOY_ALLOW_UNPUSHED=1)"
        return 1
      fi
      while IFS= read -r tracked_path; do targets+=("$tracked_path"); done <<<"$tracked"
    else
      local relative
      relative="$(git -C "$repo_root" -c core.quotepath=false ls-files --full-name --error-unmatch -- "$path" 2>/dev/null)" || {
        deploy_provenance_log "DEPLOY-BLOCK: $path is untracked — commit and push it before deploying"
        return 1; }
      targets+=("$relative")
    fi
  done

  local closure closure_path relative listed
  closure="$(_deploy_provenance_closure "$repo_root" "$reference")" || {
    deploy_provenance_log "DEPLOY-BLOCK: cannot resolve the deploy helpers under $repo_root"; return 1; }
  listed=$'\n'"$(printf '%s\n' "${targets[@]}")"$'\n'
  while IFS= read -r closure_path; do
    [[ -n "$closure_path" ]] || continue
    relative="$(git -C "$repo_root" -c core.quotepath=false ls-files --full-name --error-unmatch -- "$closure_path" 2>/dev/null)" || {
      deploy_provenance_log "DEPLOY-BLOCK: $closure_path runs in this deploy but is untracked — commit and push it first"
      return 1; }
    [[ "$listed" == *$'\n'"$relative"$'\n'* ]] && continue
    targets+=("$relative")
    listed+="$relative"$'\n'
  done <<<"$closure"

  local local_blob reference_blob
  for relative in "${targets[@]}"; do
    local_blob="$(git -C "$repo_root" hash-object -- "$repo_root/$relative" 2>/dev/null)" || {
      deploy_provenance_log "DEPLOY-BLOCK: cannot hash $relative"; return 1; }
    reference_blob="$(git -C "$repo_root" rev-parse --verify --quiet "$reference:$relative")" || {
      deploy_provenance_log "DEPLOY-BLOCK: $relative is missing from $reference — push it before deploying"
      return 1; }
    if [[ "$local_blob" != "$reference_blob" ]]; then
      deploy_provenance_log "DEPLOY-BLOCK: $relative differs from $reference — commit and push it first"
      deploy_provenance_log "  prod would run code absent from git, and the next clean deploy would revert it"
      deploy_provenance_log "  (sandbox testing only: re-run with DEPLOY_ALLOW_UNPUSHED=1)"
      return 1
    fi
  done
  deploy_provenance_log "OK: ${#targets[@]} file(s) match $reference"
  return 0
}

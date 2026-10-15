#!/usr/bin/env bash
# automation/skill_mount_probe.sh — do the mounted skills still carry the release's
# content? Sourced by healthcheck.sh (the DETECT half of "커밋됨 ≠ 배포됨").
#
# WHY it is its own file: healthcheck.sh sits a few lines under the 250 pure-LOC gate
# and the probe plus its recovery text do not fit. Same reason — and same shape — as
# checkout_mirror_probe.sh.
#
# WHY it exists at all: merging moves the release (a git sha); it never moves a skill
# mount (a content hash). The two converge independently, so a skill can sit on stale
# content while the release is already at origin/main — and unlike a blocked ff-pull,
# that state makes NO noise. Measured 2026-08-01: the release was at 79faef4
# (= origin/main) while calendar·prompt·recall·todo·wiki had been stale for two days,
# left behind by a partial deploy of 611595f (doctype·mail went, the rest did not).
# A partial deploy is the quietest kind: a deploy did happen, so nobody looks again.
#
# The verdict itself lives in skill_mount_drift.py so it is pinned by unit tests
# instead of by this shell. Read-only: it never deploys and never writes.

# Self-contained on purpose (checkout_mirror_probe.sh's shape): it resolves the runtime
# root itself rather than leaning on the sourcing script, so a caller can ship it by
# value the way land.sh does with the checkout probe.

# shellcheck source=automation/runtime_root.sh
source "$(dirname "${BASH_SOURCE[0]}")/runtime_root.sh"

# Print the drift report; rc 0 = clean, 1 = drift, 4 = could not judge (fail-closed).
skill_mount_verdict() {
  local runtime="$1" live="$2"
  # Both must be absolute — a relative path here would silently judge the wrong tree.
  [[ "$runtime" == /* && "$live" == /* ]] || { printf 'SKILL-MOUNT-UNVERIFIABLE: 절대경로가 아님\n'; return 4; }
  python3 "${runtime}/automation/skill_mount_drift.py" \
    --runtime-root "$runtime" --live-root "$live" 2>&1
}

skill_mount_log() {
  printf '[healthcheck] %s\n' "$1"
}

# The reflexive repair for a failing check is to patch code. That is wrong here and
# would waste a cycle chasing a bug that does not exist — the code is fine, it just
# is not mounted. Say so before anything else.
skill_mount_guidance() {
  printf '%s\n' \
    "이것은 코드 결함이 아니라 배포 누락이다 — 패치하지 말 것." \
    "머지는 릴리스(git sha)만 수렴시키고 스킬 마운트(내용 해시)는 건드리지 않는다." \
    "조치: 어긋난 스킬마다 automation/deploy-skill.sh <skill> 을 돌려 소유자 ✅ 뒤 마운트한다." \
    "판정: readlink /srv/autophagy-skills/live/<skill> 의 해시 == 릴리스 skills/<skill> 의 내용 해시."
}


# The DETECT half, kept here so healthcheck.sh stays under its 250 pure-LOC gate.
# A verdict of "cannot judge" (rc 4) fails too: here a silent PASS is the failure
# mode, not the safe one - the whole point is that this drift is otherwise silent.
probe_skill_mounts_current() {
  local node="$1" account="$2" target="$3" output status=0
  # Same escape hatch as HEALTHCHECK_OPS_CHECKOUT: this probe runs locally, so tests
  # (and a dry run on another host) need to point it at a tree that is not /srv.
  local live="${HEALTHCHECK_SKILL_LIVE_ROOT:-$target}"
  output="$(skill_mount_verdict "$(autophagy_runtime_root)" "$live")" || status=$?
  (( status == 0 )) && return 0
  if (( status == 1 )) \
    && [[ "$(readlink -e -- "$(autophagy_runtime_root)" 2>/dev/null)" == \
          "$(readlink -e -- "${HEALTHCHECK_RELEASE_SOURCE_ROOT:-${RUNTIME_RELEASE_CURRENT:-/srv/autophagy-agent-current}}" 2>/dev/null)" ]] \
    && python3 "$(dirname "${BASH_SOURCE[0]}")/release_switch_grace.py" \
      --current "${HEALTHCHECK_RELEASE_SOURCE_ROOT:-${RUNTIME_RELEASE_CURRENT:-/srv/autophagy-agent-current}}" \
      --live-root "$live"; then
    return 0
  fi
  while IFS= read -r line; do [[ -n "$line" ]] && skill_mount_log "$line"; done <<< "$output"
  return 1
}

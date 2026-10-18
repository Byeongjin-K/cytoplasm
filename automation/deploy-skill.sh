#!/usr/bin/env bash
# automation/deploy-skill.sh — W1-8 skill deploy pipeline (v2.4 managed activation review gate).
#
# Pipeline (enforced order, fail-closed):
#   ① SANDBOX  — skill copied to the *peer* instance: every shipped module is first
#                parsed by the peer's hermes runtime CPython (py3.11 — the cron
#                interpreter, NOT the dev/CI py3.12), then its scenario runs with
#                DUMMY secrets under a disposable HOME, making the account's
#                ~/.env.secrets unreachable; env -i alone is insufficient because
#                credential loaders fall back to $HOME. Any failure blocks the
#                pipeline BEFORE the approval stage (exit 2).
#   ② REVIEW    — deterministic, hash-bound review via automation/skill_review.py
#                records PASS/FAIL on the agent. The peer's already-successful
#                DUMMY-secret scenario output is reused; untrusted code does
#                not execute a second time on agent. Any failure exits 5.
#   ③ REQUEST + PEER-ATTEST — the agent posts the request; peer independently
#                reviews its retained sandbox copy and replies as its own bot.
#                Owner ✅ (or E2E injection) AND peer attestation are required.
#   ④ MOUNT    — current digest, agent review, peer attestation, and owner
#                approval are all rechecked before the reviewed artifact is
#                published by the root-owned store helper; Hermes discovery and an
#                invoke smoke against the live store run.
#
# Usage:
#   deploy-skill.sh <skill> [--request-only] [--approve-only] [--fresh] [--sandbox-only] [--remove] [--release-approval]
#                           [--activate-managed <quarantine-dir>]
#   deploy-skill.sh --personal <name> [--request-only] [--approve-only] [--fresh] [--sandbox-only]
#   --approve-only          verify approval only; do not mount the skill
#
# Env:
#   DEPLOY_SSH_HOST      ssh host running agent/peer (default from node config; "" = local)
#   SKILL_SRC_DIR        internal staged-source override; personal authors use --personal
#   E2E_TEST_MODE=1      regression-only signed-injection approval path. NEVER
#                        set on the production agent gateway (its unit refuses it).
#   SKILL_PROPOSAL_SOURCE  "auto" enables the max-3-per-week proposal limit.
#   APPROVAL_MESSAGE_ID  check an existing request (requires DEPLOY_NONCE too).
#   DEPLOY_NONCE         nonce bound to APPROVAL_MESSAGE_ID.
#
# Exit codes: 0 ok | 1 approval absent/invalid (no mount) | 2 sandbox block
#             3 weekly auto-proposal rate limit | 4 usage/env error or a collision
#               with a self-authored skill on the agent/peer account
#             5 review missing/failed/hash-mismatched (no mount)
#             6 approval-lifecycle refusal (an existing live request is preserved)
#             8 another execution holds this skill's refresh-to-consume lease
#             9 owner cancelled the deploy (⛔ on the request; do NOT retry)
set -euo pipefail
# 봉인된 릴리스 안에서 root 로 돌 때 CPython 이 __pycache__ 를 남기면 release-provenance
# 의 트리 동일성 검사가 다음 배포를 영구히 막는다(2026-08-03 실측). root 는 읽기 전용
# 디렉터리에 막히지 않으므로 권한이 아니라 이 스위치가 유일한 방어다.
export PYTHONDONTWRITEBYTECODE=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
STORE_ROOT="$NODE_SKILL_STORE"
# DG-4: resolve the runtime root (release `current`, else the resident mirror).
# shellcheck source=automation/runtime_root.sh
source "$REPO_ROOT/automation/runtime_root.sh"
source "$REPO_ROOT/automation/deploy_provenance.sh"
RELEASE_CURRENT="$NODE_RELEASE_CURRENT"
# Resolve the runtime root once (release `current` if present, else the mirror);
# the ABI scan below runs the shared library from wherever the runtime truly is.
RUNTIME_ROOT="$(autophagy_runtime_root)"
# The trust-critical peer_attest / execution-lock / scenario run via `run_as` ON
# THE NODE, so their runtime root must be resolved node-side (not from the
# workstation-resolved $RUNTIME_ROOT, which would stat a node path on the wrong
# host). This snippet is embedded verbatim in those node-side shells and prefers
# the release `current` symlink, else the resident mirror (a no-op until DG-5).
NODE_RUNTIME_ROOT_SNIPPET="node_runtime_root(){ if [ -e $NODE_RELEASE_CURRENT ]; then printf %s $NODE_RELEASE_CURRENT; else printf %s $NODE_DEPLOY_CHECKOUT; fi; }"
SSH_HOST="${DEPLOY_SSH_HOST-$NODE_DEPLOY_SSH_HOST}"
[[ "$(hostname -s 2>/dev/null)" == "$SSH_HOST" ]] && SSH_HOST=""
PEER_ATTEST_PUBLIC_KEY="/etc/autophagy/peer-attest-${NODE_PEER_ACCOUNT}.pub"
PROVENANCE_FILE=""
PROVENANCE_REMOTE=""

log() { printf '[deploy-skill] %s\n' "$*" >&2; }
die() { log "ERROR: $1"; exit "${2:-4}"; }

run_as() { # run_as <account> <script>  (stdin passes through for file pushes)
  local acct="$1" script="$2"
  if [[ -n "$SSH_HOST" ]]; then
    ssh "$SSH_HOST" "sudo -n -u $acct -H bash -c $(printf '%q' "$script")"
  else
    sudo -n -u "$acct" -H bash -c "$script"
  fi
}

# WHY the chmod before every rm below: skill trees are shipped read-only (E9 seals
# releases 0555/0444) and tar PRESERVES those modes, so the previous deploy's copy
# lands unwritable — and a directory without write permission cannot have its own
# entries removed, even by their owner. Re-deploying then died on "Permission denied"
# in both the peer sandbox and the agent review staging (measured 2026-08-04).
# `2>/dev/null` keeps a missing path (the common first-deploy case) silent.
#: 마운트에서 빼는 파일 이름을 **`skill_review` 에서 읽는다** — 여기 베껴 적으면 digest 와
#: 아카이브가 갈라져, 승인 해시가 덤지 않는 파일이 노드에 올라간다(공급망 구멍).
skill_mount_excludes() { # 쉼표 목록, 읽지 못하면 배포를 멈춘다
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c \
    'from automation.skill_review import MOUNT_EXCLUDED_BASENAMES as e; print(",".join(sorted(e)))' \
    || die "DEPLOY-BLOCK: could not read the mount exclusion set"
}

skill_archive_stream() { # skill_archive_stream <src_dir> <name>
  local src="$1" name="$2"
  local excluded
  excluded="$(skill_mount_excludes)" || return 1
  if [[ "${PERSONAL:-0}" == 1 ]]; then
    [[ -n "${PERSONAL_HEAD_SHA:-}" ]] || die "PERSONAL-BLOCK: committed HEAD is unavailable"
    # 개인 저작은 스킬 디렉터리가 그 리포의 루트라 제외 pathspec 이 바로 먹는다.
    local -a pathspec=(".")
    local name_only
    for name_only in ${excluded//,/ }; do pathspec+=(":(exclude)$name_only"); done
    git -C "$src" archive --format=tar --prefix="$name/" "$PERSONAL_HEAD_SHA" -- "${pathspec[@]}" | gzip -c
    return $?
  fi
  DEPLOY_ARCHIVE_EXCLUDE_BASENAMES="$excluded" \
    deploy_archive_stream "$REPO_ROOT" "$(dirname "$src")" "$name"
}

push_skill() { # push_skill <account> <src_dir> <name>
  local acct="$1" src="$2" name="$3"
  [[ "$(basename "$src")" == "$name" ]] || die "source dir basename must equal skill name"
  skill_archive_stream "$src" "$name" \
    | run_as "$acct" "chmod -R u+w \"\$HOME/.hermes/skills/$name\" 2>/dev/null; rm -rf \"\$HOME/.hermes/skills/$name\" && mkdir -p \"\$HOME/.hermes/skills\" && tar -xzf - -C \"\$HOME/.hermes/skills\""
}

PEER_STAGING_PUSHED=0

# Staged trees arrive SEALED — E9 seals releases 0555/0444 and tar preserves those modes —
# and a directory without write permission cannot have its own entries removed, even by
# their owner. push_skill unseals for exactly that reason; every cleanup path needs the
# same treatment or the copy silently survives (measured 2026-08-15: coordination, prompt
# and wiki were still staged on peer from 2026-08-01/08-04, all dr-xr-xr-x).
remove_peer_skill_copy() { # remove_peer_skill_copy <name>
  run_as "$NODE_PEER_ACCOUNT" "chmod -R u+w \"\$HOME/.hermes/skills/$1\" 2>/dev/null; rm -rf \"\$HOME/.hermes/skills/$1\"" || true
}

# Peer staging must not outlive this execution on ANY exit path, so the EXIT trap owns the
# guarantee. Guarded, because that same root holds peer's OWN self-authored skills: a run
# that never staged must remove nothing. Idempotent, so the explicit call sites below do
# not pay a second round-trip.
cleanup_peer_staging() {
  [[ "$PEER_STAGING_PUSHED" == 1 ]] || return 0
  PEER_STAGING_PUSHED=0
  remove_peer_skill_copy "$SKILL"
}

# ---------- SS-1: coexistence with agent/peer self-authored skills ----------
# The agent's primary Hermes root is being inverted: $NODE_AGENT_HOME/.hermes/skills stops being
# a read-only bind mount of the governed store and becomes the agent's OWN writable space
# (self-authored skills, audited after the fact, no per-write approval), while the governed
# store stays discoverable through skills.external_dirs. Hermes already refuses
# `skill_manage(create)` for a name that exists in any root, which covers self→governed;
# these guards cover governed→self. Fail-closed on both sides: a root that cannot be read
# or classified blocks the deploy rather than mounting over the account's own work.
self_skill_collision_block() { # self_skill_collision_block <account> <evidence>
  die "SELF-SKILL-COLLISION-BLOCK: $SKILL already exists as a self-authored skill on $1 ($2). Resolve it, then re-run this deploy: as $1 run 'hermes curator archive $SKILL', or have the owner remove that directory from the account's ~/.hermes/skills" 4
}

# `mountpoint` separates the two topologies with no guesswork. While the read-only bind
# mount is still installed EVERY governed name is present under the agent root, so a bare
# existence check would refuse every re-deploy of a live skill; once the node is
# re-provisioned and that mount is gone the root is self-authored space only and this guard
# becomes live. Depth 2 is included because Hermes files self-authored skills under a
# category directory (measured on peer: software-development/autophagy-interop).
assert_agent_root_has_no_self_skill() {
  local state
  state="$(run_as "$NODE_AGENT_ACCOUNT" "if mountpoint -q \"\$HOME/.hermes/skills\"; then printf governed-mount; elif find \"\$HOME/.hermes/skills\" -mindepth 1 -maxdepth 2 -type d -name '$SKILL' -print -quit 2>/dev/null | grep -q .; then printf present; else printf absent; fi")" \
    || die "SELF-SKILL-COLLISION-BLOCK: cannot read the agent skill root for $SKILL (ssh/sudo denied)" 4
  case "$state" in
    absent) ;;
    governed-mount) log "agent skill root is still the read-only governed bind mount; no self-authored space to collide with yet" ;;
    present) self_skill_collision_block "$NODE_AGENT_ACCOUNT" "$NODE_AGENT_HOME/.hermes/skills/$SKILL" ;;
    *) die "SELF-SKILL-COLLISION-BLOCK: agent skill root probe returned an invalid state" 4 ;;
  esac
}

# Peer's ~/.hermes/skills is a NORMAL writable root: this pipeline's sandbox staging area
# AND the home of peer's own self-authored skills. push_skill overwrites whatever stands
# there, so overwriting is only defensible when the thing standing there is our own
# leftover. `.usage.json` is Hermes' own registry (created_by "agent" is the account's word
# that it wrote that skill) and `author: autophagy-agents` in the frontmatter is this
# repo's signature. Everything else is foreign — including an unreadable or unparseable
# record, which exits non-zero here and blocks at the call site.
peer_skill_root_state() {
  run_as "$NODE_PEER_ACCOUNT" "python3 - \"\$HOME/.hermes/skills\" \"$SKILL\" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
name = sys.argv[2]
staged = root / name
# 아카이브된 스킬은 활성이 아니다. pathlib 의 glob 은 숨김 디렉터리도 매칭하므로
# .archive/<name> 이 중첩 충돌로 잡혔고, 그래서 이 가드가 안내하는 해법
# (hermes curator archive <name>)이 디렉터리를 .archive 로 옮길 뿐이라 판정이
# foreign 에서 바뀌지 않아 안내대로 해도 영영 풀리지 않았다(2026-08-20 실측).
# 이 블록은 셸 이중따옴표 안에 산다 — 백틱은 명령 치환이고 한 줄 형태를 유지한다.
nested = [path for path in sorted(root.glob(\"*/\" + name)) if path.is_dir() and not path.parent.name.startswith(\".\")]
if not nested and not staged.is_dir():
    print(\"absent\")
    raise SystemExit(0)
usage = root / \".usage.json\"
record = json.loads(usage.read_text(encoding=\"utf-8\")).get(name) if usage.is_file() else None
if record is not None and not isinstance(record, dict):
    raise SystemExit(\"unreadable .usage.json record for \" + name)
if isinstance(record, dict) and (record.get(\"created_by\") == \"agent\" or record.get(\"agent_created\") is True):
    print(\"agent-created\")
    raise SystemExit(0)
if nested or not staged.is_dir():
    print(\"foreign\")
    raise SystemExit(0)
parts = (staged / \"SKILL.md\").read_text(encoding=\"utf-8\").split(\"---\n\")
if len(parts) < 3:
    raise SystemExit(\"staged SKILL.md carries no frontmatter\")
signed = any(line.strip() == \"author: autophagy-agents\" for line in parts[1].splitlines())
print(\"staging-residue\" if signed else \"foreign\")
PY
"
}

install_reviewed_skill() {
  local src="$1" name="$2" digest="$3"
  if [[ -n "$SSH_HOST" ]]; then
    skill_archive_stream "$src" "$name" \
      | ssh -o ClearAllForwardings=yes "$SSH_HOST" "sudo -n /usr/local/libexec/autophagy-install-skill install --skill '$name' --hash '$digest'"
  else
    skill_archive_stream "$src" "$name" \
      | sudo -n /usr/local/libexec/autophagy-install-skill install --skill "$name" --hash "$digest"
  fi
}

install_managed_skill() {
  local src="$1" publisher="$2" name="$3" digest="$4"
  if [[ -n "$SSH_HOST" ]]; then
    skill_archive_stream "$src" "$name" \
      | ssh -o ClearAllForwardings=yes "$SSH_HOST" "sudo -n /usr/local/libexec/autophagy-install-skill install-managed --publisher '$publisher' --skill '$name' --hash '$digest'"
  else
    skill_archive_stream "$src" "$name" \
      | sudo -n /usr/local/libexec/autophagy-install-skill install-managed --publisher "$publisher" --skill "$name" --hash "$digest"
  fi
}

remove_live_skill() {
  local name="$1"
  if [[ -n "$SSH_HOST" ]]; then
    ssh -o ClearAllForwardings=yes "$SSH_HOST" "sudo -n /usr/local/libexec/autophagy-install-skill remove --skill '$name'"
  else
    sudo -n /usr/local/libexec/autophagy-install-skill remove --skill "$name"
  fi
}

stage_review_source() { # stage_review_source <src_dir> <name> <digest>
  local src="$1" name="$2" digest="$3"
  skill_archive_stream "$src" "$name" \
    | run_as "$NODE_AGENT_ACCOUNT" "chmod -R u+w \"\$HOME/.hermes/skill-gate/review-staging/$name\" 2>/dev/null; rm -rf \"\$HOME/.hermes/skill-gate/review-staging/$name\" && mkdir -p \"\$HOME/.hermes/skill-gate/review-staging/$name/$digest\" && tar -xzf - -C \"\$HOME/.hermes/skill-gate/review-staging/$name/$digest\""
}

save_review_scenario() { # save_review_scenario <output> <name> <digest>
  local output="$1" name="$2" digest="$3"
  printf '%s' "$output" \
    | run_as "$NODE_AGENT_ACCOUNT" "umask 077; mkdir -p \"\$HOME/.hermes/skill-gate/review-staging/$name/$digest\"; cat > \"\$HOME/.hermes/skill-gate/review-staging/$name/$digest/scenario.out\"; chmod 600 \"\$HOME/.hermes/skill-gate/review-staging/$name/$digest/scenario.out\""
}

cleanup_review_staging() { # cleanup_review_staging <name>
  run_as "$NODE_AGENT_ACCOUNT" "chmod -R u+w \"\$HOME/.hermes/skill-gate/review-staging/$1\" 2>/dev/null; rm -rf \"\$HOME/.hermes/skill-gate/review-staging/$1\"" || true
}

skill_digest() { # deterministic content hash of the skill dir
  if [[ "${PERSONAL:-0}" == 1 ]]; then
    local staged rc=0
    staged="$(mktemp -d)" || return 1
    skill_archive_stream "$1" "$SKILL" | tar -xzf - -C "$staged" || rc=$?
    if [[ "$rc" == 0 ]]; then
      PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c 'from pathlib import Path; from automation.skill_review import skill_digest; import sys; print(skill_digest(Path(sys.argv[1])))' "$staged/$SKILL" || rc=$?
    fi
    rm -rf "$staged"
    return "$rc"
  fi
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c 'from pathlib import Path; from automation.skill_review import skill_digest; import sys; print(skill_digest(Path(sys.argv[1])))' "$1"
}

hermes_lists_skill() { # hermes_lists_skill <account> <name> -> exit 0 when listed
  # `hermes skills list` renders a BORDERED TABLE, so a whole-line match (-Fx) can
  # never succeed — a824c4f introduced one and silently blocked every deploy at
  # SANDBOX until 2026-08-04 (measured: even an installed skill returned exit 1).
  # Match the NAME CELL instead: the left border, the name, then padding. That still
  # refuses `managed-X` when asked for `X`, which is what a824c4f wanted.
  # Known limit: names wider than the column are rendered truncated with `…` and will
  # not match — no shipped skill name is that long, and a miss fails CLOSED (blocks).
  run_as "$1" "cd ~ && export PATH=\"\$HOME/.local/bin:\$PATH\" && hermes skills list 2>/dev/null | grep -Fq \"│ $2 \""
}

gate() { # gate <extra-env> <gate-args...> — runs skill_gate.py as agent with its secrets
  local extra="$1"; shift
  run_as "$NODE_AGENT_ACCOUNT" "set -a; . \"\$HOME/.env.secrets\"; set +a; $extra python3 \"\$HOME/.hermes/skill-gate/skill_gate.py\" $*"
}

refresh_gate() { # refresh_gate <gate-args...> — owner/binding preflight without approval logging
  run_as "$NODE_AGENT_ACCOUNT" "set -a; . \"\$HOME/.env.secrets\"; set +a; PYTHONPATH=\"\$HOME/.hermes/interop_runtime\" python3 \"\$HOME/.hermes/interop_runtime/automation/skill_gate_refresh.py\" $*"
}

review() { # review <review-args...> — runs skill_review.py as agent with its gate directory
  run_as "$NODE_AGENT_ACCOUNT" "set -a; . \"\$HOME/.env.secrets\"; set +a; python3 \"\$HOME/.hermes/skill-gate/skill_review.py\" $*"
}

peer_attest() { # peer_attest <skill> <digest> <request-message-id> <deploy-nonce> <channel-id> [--refresh]
  local name="$1" digest="$2" message_id="$3" nonce="$4" channel="$5" refresh_arg=""
  case "${6:-}" in
    "") ;;
    --refresh) refresh_arg="--refresh" ;;
    *) die "invalid peer attestation mode" ;;
  esac
  run_as "$NODE_PEER_ACCOUNT" "$NODE_RUNTIME_ROOT_SNIPPET; RR=\$(node_runtime_root); set -a; . \"\$HOME/.env.secrets\"; set +a; exec python3 \"\$RR/automation/peer_attest.py\" --skill \"$name\" --staged-dir \"\$HOME/.hermes/skills/$name\" --hash \"$digest\" --request-message-id \"$message_id\" --deploy-nonce \"$nonce\" --channel-id \"$channel\" --mode \"$NODE_PEER_ATTEST_MODE\" $refresh_arg"
}

PEER_ATTEST_BLOB=""

gate_check() {
  local extra="$1"; shift
  case "$NODE_PEER_ATTEST_MODE" in
    discord)
      gate "$extra" check "$@" --peer-attest-mode discord
      ;;
    signed)
      printf '%s\n' "$PEER_ATTEST_BLOB" \
        | gate "$extra" check "$@" --peer-attest-mode signed \
            --peer-attest-public-key "$PEER_ATTEST_PUBLIC_KEY" --peer-attestation-stdin
      ;;
    *)
      die "PEER-ATTEST-BLOCK: peer_attest_mode must be discord or signed"
      ;;
  esac
}

refresh_gate_check() {
  case "$NODE_PEER_ATTEST_MODE" in
    discord)
      refresh_gate "$@" --peer-attest-mode discord
      ;;
    signed)
      printf '%s\n' "$PEER_ATTEST_BLOB" \
        | refresh_gate "$@" --peer-attest-mode signed \
            --peer-attest-public-key "$PEER_ATTEST_PUBLIC_KEY" --peer-attestation-stdin
      ;;
    *)
      die "PEER-ATTEST-BLOCK: peer_attest_mode must be discord or signed"
      ;;
  esac
}

EXECUTION_LOCK_PID=""
EXECUTION_LOCK_READ_FD=""
EXECUTION_LOCK_WRITE_FD=""

start_execution_lock() {
  local status="" rc=8
  coproc DEPLOY_EXECUTION_LEASE {
    run_as "$NODE_AGENT_ACCOUNT" "$NODE_RUNTIME_ROOT_SNIPPET; RR=\$(node_runtime_root); PYTHONPATH=\"\$RR\" exec python3 \"\$RR/automation/deploy_execution_lock.py\" --skill \"$SKILL\""
  }
  EXECUTION_LOCK_PID="$DEPLOY_EXECUTION_LEASE_PID"
  EXECUTION_LOCK_READ_FD="${DEPLOY_EXECUTION_LEASE[0]}"
  EXECUTION_LOCK_WRITE_FD="${DEPLOY_EXECUTION_LEASE[1]}"
  if ! IFS= read -r status <&"$EXECUTION_LOCK_READ_FD"; then
    wait "$EXECUTION_LOCK_PID" || rc=$?
    eval "exec ${EXECUTION_LOCK_READ_FD}<&-" 2>/dev/null || true
    eval "exec ${EXECUTION_LOCK_WRITE_FD}>&-" 2>/dev/null || true
    EXECUTION_LOCK_PID="" EXECUTION_LOCK_READ_FD="" EXECUTION_LOCK_WRITE_FD=""
    die "EXECUTION-LOCK-BLOCK: another deploy is refreshing or mounting $SKILL" "$rc"
  fi
  eval "exec ${EXECUTION_LOCK_READ_FD}<&-" 2>/dev/null || true
  EXECUTION_LOCK_READ_FD=""
  [[ "$status" == "EXECUTION-LOCK-ACQUIRED skill=$SKILL" ]] \
    || die "EXECUTION-LOCK-BLOCK: invalid acquisition response"
}

release_execution_lock() {
  [[ -n "$EXECUTION_LOCK_PID" ]] || return 0
  eval "exec ${EXECUTION_LOCK_WRITE_FD}>&-" 2>/dev/null || true
  wait "$EXECUTION_LOCK_PID" || true
  EXECUTION_LOCK_PID="" EXECUTION_LOCK_WRITE_FD=""
}

check_with_attestation_refresh() {
  local digest="$1" approved=0 check_env=""
  local -a injection_args=()
  if [[ "${E2E_TEST_MODE:-}" == "1" ]]; then
    check_env="$E2E_ENV"
    injection_args=(--injection-file "\$HOME/.hermes/skill-gate/injected-approval.json")
  fi
  gate_check "$check_env" --skill "$SKILL" --hash "$digest" --message-id "$MESSAGE_ID" \
    --deploy-nonce "$DEPLOY_NONCE" "${PROVENANCE_REQUEST_ARGS[@]}" "${injection_args[@]}" >&2 \
    || approved=$?
  if [[ "$approved" == 1 ]]; then
    approved=0
    refresh_gate_check --skill "$SKILL" --hash "$digest" --message-id "$MESSAGE_ID" \
      --deploy-nonce "$DEPLOY_NONCE" "${PROVENANCE_REQUEST_ARGS[@]}" "${injection_args[@]}" >&2 \
      || approved=$?
  fi
  if [[ "$approved" == 7 ]]; then
    PEER_ATTEST_BLOB="$(peer_attest "$SKILL" "$DIGEST" "$MESSAGE_ID" "$DEPLOY_NONCE" "$DEPLOY_APPROVALS_CHANNEL_ID" --refresh)" \
      || die "PEER-ATTEST-BLOCK: independent peer refresh failed" 5
    if [[ "$NODE_PEER_ATTEST_MODE" == "discord" ]]; then
      printf '%s\n' "$PEER_ATTEST_BLOB" >&2
      PEER_ATTEST_BLOB=""
    fi
    approved=0
    gate_check "$check_env" --skill "$SKILL" --hash "$digest" --message-id "$MESSAGE_ID" \
      --deploy-nonce "$DEPLOY_NONCE" "${PROVENANCE_REQUEST_ARGS[@]}" "${injection_args[@]}" >&2 \
      || approved=$?
  fi
  return "$approved"
}

gate_release_authorize() { # gate_release_authorize <digest> — stdout: <message_id>:<release_nonce>
  case "$NODE_PEER_ATTEST_MODE" in
    discord)
      gate "" release-authorize --skill "$SKILL" --hash "$1" --peer-attest-mode discord
      ;;
    signed)
      printf '%s\n' "$PEER_ATTEST_BLOB" \
        | gate "" release-authorize --skill "$SKILL" --hash "$1" --peer-attest-mode signed \
            --peer-attest-public-key "$PEER_ATTEST_PUBLIC_KEY" --peer-attestation-stdin
      ;;
    *)
      die "PEER-ATTEST-BLOCK: peer_attest_mode must be discord or signed"
      ;;
  esac
}

release_authorize_with_refresh() { # VA-2: 릴리스 ✅ + 이 digest 커버리지 + peer 증명
  local digest="$1" approved=0
  gate_release_authorize "$digest" >/dev/null || approved=$?
  if [[ "$approved" == 7 ]]; then
    PEER_ATTEST_BLOB="$(peer_attest "$SKILL" "$DIGEST" "$MESSAGE_ID" "$DEPLOY_NONCE" "$DEPLOY_APPROVALS_CHANNEL_ID" --refresh)" \
      || die "PEER-ATTEST-BLOCK: independent peer refresh failed" 5
    if [[ "$NODE_PEER_ATTEST_MODE" == "discord" ]]; then
      printf '%s\n' "$PEER_ATTEST_BLOB" >&2
      PEER_ATTEST_BLOB=""
    fi
    approved=0
    gate_release_authorize "$digest" >/dev/null || approved=$?
  fi
  return "$approved"
}

sync_ops_checkout_for_peer_attest() { # fail-closed ops-checkout refresh so peer_attest runs current code
  # DG-4: when the immutable release runtime exists, converge it so the peer verifier
  # and the execution lease run from a sealed tree instead of the mutable mirror, whose
  # dirty/ahead state can then no longer block this deploy. Until the DG-5 node flip
  # creates `current`, fall through to the historical ff-pull path unchanged.
  local release_state
  release_state="$(run_as "$NODE_OPS_ACCOUNT" "if [ -e $RELEASE_CURRENT ]; then printf present; elif [ -L $RELEASE_CURRENT ]; then printf inaccessible; elif [ -x $NODE_SERVICE_ROOT ]; then printf absent; else exit 13; fi")" \
    || die "SYNC-BLOCK: release runtime probe failed (ops sudo/permission denied)"
  case "$release_state" in
    "present")
      if [[ "$PERSONAL" == 0 ]]; then
        local local_release_sha current_release_sha
        local_release_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)" \
          || die "SYNC-BLOCK: cannot resolve local release HEAD"
        current_release_sha="$(
          run_as "$NODE_OPS_ACCOUNT" \
            "basename \"\$(readlink -f $RELEASE_CURRENT)\""
        )" || die "SYNC-BLOCK: cannot resolve current release SHA"
        [[ "$current_release_sha" =~ ^[0-9a-f]{40}$ ]] \
          || die "SYNC-BLOCK: current release target is not a canonical SHA"
        if [[ "$current_release_sha" == "$local_release_sha" ]]; then
          log "release runtime already matches local HEAD; skipping origin/main convergence"
          scan_live_skill_abi
          return 0
        fi
      fi
      log "converging release runtime $RELEASE_CURRENT (signed release install + flip)"
      # Through the SAME argument-free privileged helper the reconciler uses, never the
      # by-value converger: that one defaults to whatever `origin/main` is right now, with
      # no signature and no release floor. Measured 2026-08-21 — production was running
      # 2c065bdf, a commit no release tag ever pointed at, installed by this very call
      # during a `meeting` skill deploy while the floor still read v1.0.24. Approving one
      # skill must not promote the whole code runtime onto an unsigned head.
      #
      # ops already holds NOPASSWD sudo for this helper, and it takes the same lock, so
      # the only thing that changes is that production can no longer move to code the
      # maintainer never signed. No signed release at origin/main HEAD => deploy stops
      # here, and the fix is to cut the release tag the merge was supposed to cut.
      run_as "$NODE_OPS_ACCOUNT" "sudo -n $NODE_LIBEXEC_DIR/autophagy-converge-origin-main" \
        || die "SYNC-BLOCK: release convergence failed (no signed release at origin/main HEAD? run automation/release-tag.sh)"
      log "release runtime converged; verifier files are the sealed read-only release"
      scan_live_skill_abi
      return 0
      ;;
    "absent")
      log "release runtime absent; using ops checkout fallback"
      ;;
    "inaccessible")
      die "SYNC-BLOCK: release runtime path is broken or inaccessible to ops"
      ;;
    *)
      die "SYNC-BLOCK: release runtime probe returned an invalid state"
      ;;
  esac
  log "syncing ops checkout $NODE_DEPLOY_CHECKOUT (ff-only) before request/attest"
  run_as "$NODE_OPS_ACCOUNT" "git -C $NODE_DEPLOY_CHECKOUT rev-parse --is-inside-work-tree >/dev/null" \
    || die "SYNC-BLOCK: $NODE_DEPLOY_CHECKOUT is not a git checkout"
  local dirty
  dirty="$(run_as "$NODE_OPS_ACCOUNT" "git -C $NODE_DEPLOY_CHECKOUT status --porcelain")" \
    || die "SYNC-BLOCK: ops checkout status probe failed"
  [[ -z "$dirty" ]] || die "SYNC-BLOCK: ops checkout is dirty (local modifications present)"
  run_as "$NODE_OPS_ACCOUNT" "git -C $NODE_DEPLOY_CHECKOUT pull --ff-only" \
    || die "SYNC-BLOCK: ff-only pull failed on ops checkout (auth/network/divergence)"
  # The peer verifier and execution lease run from this checkout. Git restores files
  # under ops's group-writable umask, so harden every executable trust-boundary input.
  run_as "$NODE_OPS_ACCOUNT" "chmod g-w,o-w $NODE_DEPLOY_CHECKOUT/automation/git_tag_signature.py $NODE_DEPLOY_CHECKOUT/automation/peer_attest.py $NODE_DEPLOY_CHECKOUT/automation/peer_attest_runtime.py $NODE_DEPLOY_CHECKOUT/automation/peer_attestation.py $NODE_DEPLOY_CHECKOUT/automation/peer_signed_attestation.py $NODE_DEPLOY_CHECKOUT/automation/skill_review.py $NODE_DEPLOY_CHECKOUT/automation/deploy_execution_lock.py $NODE_DEPLOY_CHECKOUT/automation/interop/approval_lease.py" \
    || die "SYNC-BLOCK: cannot harden verifier-file permissions on ops checkout"
  local f local_hash ops_hash
  for f in git_tag_signature.py peer_attest.py peer_attest_runtime.py peer_attestation.py peer_signed_attestation.py skill_review.py deploy_execution_lock.py interop/approval_lease.py; do
    local_hash="$(sha256sum "$REPO_ROOT/automation/$f" | cut -d' ' -f1)"
    ops_hash="$(run_as "$NODE_OPS_ACCOUNT" "cat $NODE_DEPLOY_CHECKOUT/automation/$f" | sha256sum | cut -d' ' -f1)" \
      || die "SYNC-BLOCK: cannot read ops checkout automation/$f"
    [[ "$local_hash" == "$ops_hash" ]] \
      || die "SYNC-BLOCK: automation/$f differs between local repo and ops checkout (push local commit first)"
  done
  log "ops checkout in sync (verifier files hash-match local repo)"
  scan_live_skill_abi
}

# The ff-pull above just moved the shared library under every ALREADY-live skill
# snapshot. AS-3.2 proved an unrelated deploy can break three live approval flows
# at once that way. Scan the live fleet against the freshly-pulled library and WARN
# - never die: blocking here would strand the owner approval this deploy already
# consumed, a worse failure than the fail-closed refuse-to-post the skew causes.
# The checker crashing is a WARN too; a broken guard must not break a deploy.
# DEPLOY_ABI_STRICT=1 escalates to a block once the false-positive rate is known.
scan_live_skill_abi() {
  local out rc=0
  # Resolve the runtime root here so the function is self-contained (the isolated
  # scan harness does not set the script global); default to the mirror.
  local runtime_root="${RUNTIME_ROOT:-$NODE_DEPLOY_CHECKOUT}"
  out="$(run_as "$NODE_OPS_ACCOUNT" "python3 -I $runtime_root/automation/skill_library_abi.py ${STORE_ROOT}/live $runtime_root/automation" 2>&1)" || rc=$?
  if (( rc == 0 )); then
    return 0
  fi
  log "MOUNT-ABI-WARN: a live skill can no longer call the pulled shared library"
  while IFS= read -r line; do [[ -n "$line" ]] && log "MOUNT-ABI-WARN: ${line}"; done <<< "$out"
  if [[ "${DEPLOY_ABI_STRICT:-}" == "1" ]]; then
    die "MOUNT-ABI-BLOCK: DEPLOY_ABI_STRICT=1 and a live skill has an ABI break" 4
  fi
  return 0
}

cleanup_e2e_injection() {
  [[ "${E2E_TEST_MODE:-}" == "1" ]] || return 0
  run_as "$NODE_AGENT_ACCOUNT" "rm -f \"\$HOME/.hermes/skill-gate/e2e.secret\" \"\$HOME/.hermes/skill-gate/injected-approval.json\"" || true
}

cleanup_deploy_provenance() {
  [[ -n "$PROVENANCE_FILE" ]] && rm -f "$PROVENANCE_FILE"
  [[ -n "$PROVENANCE_REMOTE" ]] || return 0
  run_as "$NODE_AGENT_ACCOUNT" "rm -f \"$PROVENANCE_REMOTE\"" || true
}

cleanup_deploy_temps() {
  release_execution_lock
  cleanup_peer_staging
  cleanup_e2e_injection
  cleanup_deploy_provenance
}

trap cleanup_deploy_temps EXIT

# ---------- argument parsing ----------
PERSONAL=0
if [[ "${1:-}" == "--personal" ]]; then
  [[ $# -ge 2 ]] || die "--personal requires a skill name"
  PERSONAL=1
  SKILL="$2"
  shift 2
else
  SKILL="${1:-}"
  shift || true
fi
[[ -n "$SKILL" ]] || die "usage: deploy-skill.sh <skill> [...] | deploy-skill.sh --personal <name> [...]"
[[ "$SKILL" =~ ^[a-z0-9][a-z0-9-]{1,40}$ ]] || die "invalid skill name: $SKILL"
REQUEST_ONLY=0 APPROVE_ONLY=0 FRESH=0 SANDBOX_ONLY=0 REMOVE=0 RELEASE_APPROVAL=0
ACTIVATE_MANAGED=0
QUARANTINE_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --request-only) REQUEST_ONLY=1 ;;
    --approve-only) APPROVE_ONLY=1 ;;
    --release-approval) RELEASE_APPROVAL=1 ;;
    --fresh) FRESH=1 ;;
    --sandbox-only) SANDBOX_ONLY=1 ;;
    --remove) REMOVE=1 ;;
    --activate-managed)
      [[ $# -ge 2 ]] || die "--activate-managed requires a quarantine directory"
      ACTIVATE_MANAGED=1
      QUARANTINE_DIR="$2"
      shift 2
      continue
      ;;
    *) die "unknown flag: $1" ;;
  esac
  shift
done

if [[ "$PERSONAL" == 1 && "$SKILL" == managed-* ]]; then
  die "MANAGED-BLOCK: personal skills cannot use the reserved managed- prefix"
fi

if [[ "$REMOVE" == 1 ]]; then
  remove_live_skill "$SKILL" || die "privileged skill removal failed"
  remove_peer_skill_copy "$SKILL"
  for acct in "$NODE_AGENT_ACCOUNT" "$NODE_PEER_ACCOUNT"; do
    if hermes_lists_skill "$acct" "$SKILL"; then die "removal failed: $SKILL still listed on $acct"; fi
    log "REMOVED $SKILL from $acct (hermes skills list: absent)"
  done
  exit 0
fi

# `publish-` is reserved, for the same reason `managed-` is. A skill by that name would
# write its pending approval record to <gate_dir>/pending/publish-<name>.json — exactly
# the path the publish gate uses for skill <name> — so two different owner
# authorisations would contend for one file on the path that mounts code.
#
# Unconditional, unlike the managed guard below: a managed name is legal and merely
# needs the right flag, whereas this name is illegal under the current record layout,
# and the collision happens when the record is WRITTEN — long before anything mounts.
if [[ "$SKILL" == publish-* ]]; then
  die "RESERVED-BLOCK: 'publish-' is a reserved skill-name prefix (approval record collision)"
fi

if [[ "$ACTIVATE_MANAGED" == 0 && "$APPROVE_ONLY" == 0 && "$SANDBOX_ONLY" == 0 && "$REQUEST_ONLY" == 0 && "$SKILL" == managed-* ]]; then
  die "MANAGED-BLOCK: mounting a managed skill requires --activate-managed"
fi

if [[ "$ACTIVATE_MANAGED" == 1 ]]; then
  [[ "$SKILL" == managed-* ]] || die "MANAGED-BLOCK: --activate-managed requires a managed-* skill name"
  MANAGED_BASE="${SKILL#managed-}"
  if readlink "$STORE_ROOT/live/$MANAGED_BASE" >/dev/null; then
    die "COLLISION-BLOCK: managed skill $SKILL conflicts with live base $MANAGED_BASE; remove one with --remove"
  fi
else
  if readlink "$STORE_ROOT/live/managed-$SKILL" >/dev/null; then
    die "COLLISION-BLOCK: base skill $SKILL conflicts with live managed-$SKILL; remove one with --remove"
  fi
fi

PERSONAL_ROOT="$HOME/.hermes/personal-skills"
PERSONAL_HEAD_SHA=""
SRC_DIR="${SKILL_SRC_DIR:-$REPO_ROOT/skills/$SKILL}"
MANAGED_PUBLISHER=""
PROVENANCE_REQUEST_ARGS=()
if [[ "$PERSONAL" == 1 ]]; then
  [[ "$ACTIVATE_MANAGED" == 0 ]] || die "MANAGED-BLOCK: --personal and --activate-managed are mutually exclusive"
  [[ -z "${SKILL_SRC_DIR:-}" ]] || die "PERSONAL-BLOCK: SKILL_SRC_DIR cannot override --personal"
  SRC_DIR="$PERSONAL_ROOT/$SKILL"
  mkdir -p "$SRC_DIR" || die "PERSONAL-BLOCK: cannot create source directory: $SRC_DIR"
  PERSONAL_GIT_TOPLEVEL="$(git -C "$SRC_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -z "$PERSONAL_GIT_TOPLEVEL" || "$(readlink -f "$PERSONAL_GIT_TOPLEVEL")" != "$(readlink -f "$SRC_DIR")" ]]; then
    git -C "$SRC_DIR" init -q || die "PERSONAL-BLOCK: cannot initialize git repository: $SRC_DIR"
  fi
fi
if [[ "$ACTIVATE_MANAGED" == 1 ]]; then
  [[ -d "$QUARANTINE_DIR" ]] || die "MANAGED-BLOCK: quarantine directory missing: $QUARANTINE_DIR"
  MANAGED_SKILL_DIR="$QUARANTINE_DIR/$SKILL"
  [[ -f "$QUARANTINE_DIR/manifest.json" && -f "$QUARANTINE_DIR/provenance.json" && -f "$MANAGED_SKILL_DIR/SKILL.md" ]] \
    || die "MANAGED-BLOCK: quarantine layout requires manifest.json, provenance.json, and $SKILL/SKILL.md"
  MANAGED_FIELDS="$(PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c 'from pathlib import Path; from automation.managed_skills.manifest import manifest_digest, parse_manifest; import json, sys; quarantine = Path(sys.argv[1]); manifest = parse_manifest((quarantine / "manifest.json").read_text(encoding="utf-8")); provenance = json.loads((quarantine / "provenance.json").read_text(encoding="utf-8")); valid = isinstance(provenance, dict) and provenance.get("publisher") == manifest.publisher and provenance.get("sequence") == manifest.release_sequence and provenance.get("tag") == f"{manifest.skill}/v{manifest.release_sequence}"; valid or sys.exit(1); print(manifest.publisher, manifest.skill, manifest.skill_sha256, manifest.release_sequence, provenance["tag"], manifest_digest(manifest), sep="\n")' "$QUARANTINE_DIR")" \
    || die "MANAGED-BLOCK: quarantine manifest/provenance is invalid"
  mapfile -t MANAGED_FIELDS <<<"$MANAGED_FIELDS"
  [[ "${#MANAGED_FIELDS[@]}" == 6 ]] || die "MANAGED-BLOCK: quarantine manifest fields are incomplete"
  MANAGED_PUBLISHER="${MANAGED_FIELDS[0]}"
  MANAGED_SKILL="${MANAGED_FIELDS[1]}"
  MANAGED_DIGEST="${MANAGED_FIELDS[2]}"
  MANAGED_SEQUENCE="${MANAGED_FIELDS[3]}"
  MANAGED_TAG="${MANAGED_FIELDS[4]}"
  MANAGED_MANIFEST_SHA256="${MANAGED_FIELDS[5]}"
  [[ "$MANAGED_SKILL" == "$SKILL" ]] || die "MANAGED-BLOCK: manifest skill does not match requested skill"
  QUARANTINE_DIGEST="$(skill_digest "$MANAGED_SKILL_DIR")"
  [[ "$QUARANTINE_DIGEST" == "$MANAGED_DIGEST" ]] || die "MANAGED-BLOCK: quarantine skill digest does not match manifest"
  SRC_DIR="$MANAGED_SKILL_DIR"
  DIGEST="$MANAGED_DIGEST"
  PROVENANCE_FILE="$(mktemp)" || die "MANAGED-BLOCK: cannot create provenance file"
  python3 -c 'import json, sys; print(json.dumps({"publisher": sys.argv[1], "tag": sys.argv[2], "release_sequence": int(sys.argv[3]), "manifest_sha256": sys.argv[4]}, sort_keys=True, separators=(",", ":")))' \
    "$MANAGED_PUBLISHER" "$MANAGED_TAG" "$MANAGED_SEQUENCE" "$MANAGED_MANIFEST_SHA256" > "$PROVENANCE_FILE" \
    || die "MANAGED-BLOCK: cannot render provenance file"
else
  [[ -f "$SRC_DIR/SKILL.md" ]] || die "skill source missing: $SRC_DIR/SKILL.md"
  if [[ "$PERSONAL" == 1 ]]; then
    PERSONAL_HEAD_SHA="$(personal_provenance_check "$SRC_DIR")" || exit 4
    PROVENANCE_FILE="$(mktemp)" || die "PERSONAL-BLOCK: cannot create provenance file"
    printf '{"personal_head_sha":"%s"}\n' "$PERSONAL_HEAD_SHA" > "$PROVENANCE_FILE" \
      || die "PERSONAL-BLOCK: cannot render provenance file"
  fi
  DIGEST="$(skill_digest "$SRC_DIR")"
fi
log "skill=$SKILL sha256=$DIGEST"

# Deploy guard: a mounted skill must already exist in origin/main, or the next clean
# deploy from any other session silently reverts it. Sandbox-only runs, managed
# quarantine dirs and explicit SKILL_SRC_DIR overrides are exempt by design — they are
# deliberately not repository state.
if [[ "$ACTIVATE_MANAGED" == 0 && "$PERSONAL" == 0 && "$SANDBOX_ONLY" == 0 && -z "${SKILL_SRC_DIR:-}" ]]; then
  deploy_provenance_check "$REPO_ROOT" "$SRC_DIR" || exit 4
fi

# SS-1 collision guard. It covers every arm that can stage or mount (--remove returned
# above, by design: --remove is the way OUT of a collision). It sits after the purely
# LOCAL validations — usage, quarantine layout, personal provenance, digest, deploy
# provenance — because it is the first step that leaves this host: a missing quarantine
# directory must fail as itself, not as an ssh error. It still precedes stage 1, so no
# ops sync, no execution lease and nothing staged on peer happens before it.
assert_agent_root_has_no_self_skill

# ---------- stage 1: sandbox on peer with DUMMY secrets ----------
sandbox_block() { cleanup_peer_staging; die "SANDBOX-BLOCK: $1" 2; }

log "stage 1/4 SANDBOX (peer, dummy secrets)"
sync_ops_checkout_for_peer_attest
start_execution_lock
# Classify peer's root BEFORE overwriting it. The execution lease is already held, so the
# classification and the overwrite cannot interleave with another deploy.
PEER_ROOT_STATE="$(peer_skill_root_state)" \
  || die "SELF-SKILL-COLLISION-BLOCK: cannot classify the peer skill root for $SKILL (unreadable or ambiguous)" 4
case "$PEER_ROOT_STATE" in
  absent) ;;
  staging-residue) log "STAGING-RESIDUE-CLEANED: $SKILL under peer's root is this pipeline's own leftover sandbox copy; replacing it" ;;
  agent-created) self_skill_collision_block "$NODE_PEER_ACCOUNT" "hermes .usage.json records $SKILL as agent-created" ;;
  foreign) self_skill_collision_block "$NODE_PEER_ACCOUNT" "$NODE_PEER_HOME/.hermes/skills/$SKILL carries no 'author: autophagy-agents' marker" ;;
  *) die "SELF-SKILL-COLLISION-BLOCK: peer skill root probe returned an invalid state" 4 ;;
esac
# Set BEFORE the push: a push that fails midway still leaves a partial tree to remove.
PEER_STAGING_PUSHED=1
push_skill peer "$SRC_DIR" "$SKILL"

# Modules the staged gate imports at runtime. skill_gate.py imports the shared
# approval lifecycle (approval_lifecycle → approval_lease) plus its skill-gate
# adapter (skill_gate_specs → skill_gate_approval → skill_gate_request) — stage
# every one or the staged gate raises ImportError on the node and EVERY deploy
# fails closed, including the deploy that would ship the fix. automation/ and
# automation/interop/ resolve as PEP 420 namespace packages on the node, so no
# __init__.py needs staging.
GATE_HELPERS=(skill_gate.py skill_gate_refresh.py skill_gate_review.py
              typing_compat.py git_tag_signature.py peer_attestation.py peer_signed_attestation.py peer_attest_runtime.py skill_gate_e2e.py
              skill_gate_specs.py stored_content.py release_spec.py release_spec_message.py skill_gate_approval.py skill_gate_request.py
              skill_gate_retire.py skill_gate_surface.py)
GATE_INTEROP_HELPERS=(interop/approval_lease.py interop/approval_lifecycle.py interop/approval_card.py
                      interop/approval_reminder.py interop/approval_reminder_config.py
                      interop/approval_types.py interop/approval_surface.py
                      interop/owner_message.py interop/owner_message_text.py
                      interop/approval_directory.py interop/injection_adapter.py
                      interop/chunker.py)

# Notice facade import closure: separate from the gate and peer skill payload.
# tests/unit/test_deploy_staging_owner_notice.py derives coverage from imports.
OWNER_NOTICE_HELPERS=(owner_notice.py interop/origin_notice.py
                      interop/owner_message.py interop/owner_message_text.py
                      interop/discord_transport.py interop/chunker.py
                      interop/approval_directory.py interop/approval_surface.py)

validate_gate_staging_imports() {
  if ! python3 - "$REPO_ROOT" "automation/skill_gate_publish.py" \
    "automation/skill_gate.py" \
    "${GATE_HELPERS[@]/#/automation/}" \
    "${GATE_INTEROP_HELPERS[@]/#/automation/}" <<'PY'
import ast
import sys
from pathlib import Path

root = Path(sys.argv[1])
optional = {sys.argv[2]}
staged = set(sys.argv[3:])
def imports(path):
    found = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        dotted_names = []
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            dotted_names.append(node.module)
            dotted_names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            dotted_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level:
            package = path.parent
            for _ in range(node.level - 1):
                package = package.parent
            modules = [node.module] if node.module else [alias.name for alias in node.names]
            for module in modules:
                candidate = package / (module.replace(".", "/") + ".py")
                if candidate.is_file() and candidate.is_relative_to(root):
                    found.add(str(candidate.relative_to(root)))
        elif isinstance(node, ast.Call) and node.args:
            function = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            argument = node.args[0]
            if function in {"repo_module", "_repo_module"} and isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                dotted_names.append(f"automation.interop.{argument.value}")
        for dotted in dotted_names:
            if not dotted.startswith("automation."):
                continue
            candidate = root / (dotted.replace(".", "/") + ".py")
            if not candidate.is_file():
                continue
            found.add(str(candidate.relative_to(root)))
    return found

# Skill-owned approval imports are roots even if an array member was forgotten.
# Non-gate skill facilities use the full runtime; notices have a separate list.
queue = list(staged)
for scripts in (root / "skills").glob("*/scripts"):
    for path in scripts.glob("*.py"):
        queue.extend(name for name in imports(path)
                     if name.startswith("automation/interop/approval_"))
required = set()
while queue:
    relative = queue.pop()
    if relative in required:
        continue
    required.add(relative)
    queue.extend(imports(root / relative))

missing = sorted(required - staged - optional)
for module in missing:
    print(f"STAGE-BLOCK: imported gate module is not staged: {module}", file=sys.stderr)
raise SystemExit(bool(missing))
PY
  then
    die "STAGE-BLOCK: imported gate module is not staged"
  fi
}

# Preflight: fail before the first byte is copied, so a renamed or missing
# module can never leave a half-staged (import-broken) gate on the node.
for src in skill_gate.py "${GATE_HELPERS[@]}" "${GATE_INTEROP_HELPERS[@]}"; do
  [[ -f "$REPO_ROOT/automation/$src" ]] \
    || die "STAGE-BLOCK: gate module missing from checkout: automation/$src"
done
for src in "${OWNER_NOTICE_HELPERS[@]}"; do
  [[ -f "$REPO_ROOT/automation/$src" ]] \
    || die "STAGE-BLOCK: owner notice module missing from checkout: automation/$src"
done
validate_gate_staging_imports

# Runtime-Python (3.11) parse gate: hermes runs no_agent cron scripts under its
# uv-managed CPython (observed 3.11.15), NOT the dev/interactive python3 (3.12). PEP 701
# f-strings and PEP 695 `type` aliases parse clean on 3.12 yet SyntaxError at import on
# 3.11, silently killing watchers (incident t_90a2e810: triage_gate.py broke the mail
# approval watcher). The peer runs the same hermes runtime, so parse every module the
# skill ships under that exact interpreter before the 3.12 scenario below can pass it.
# Dev-time twin scan: tests/unit/test_py311_syntax_guard.py.
RUNTIME_PARSE="$(run_as "$NODE_PEER_ACCOUNT" "\"\$HOME/.hermes/hermes-agent/venv/bin/python\" - \"\$HOME/.hermes/skills/$SKILL\" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
failures = []
for path in sorted(root.rglob(\"*.py\")):
    if \"__pycache__\" in path.parts:
        continue
    try:
        compile(path.read_text(encoding=\"utf-8\"), str(path), \"exec\")
    except SyntaxError as error:
        failures.append(f\"{path.name}:{error.lineno}: {error.msg}\")
if failures:
    print(\"RUNTIME-PARSE-FAIL \" + \"; \".join(failures))
    sys.exit(1)
print(\"RUNTIME-PARSE-PASS python\" + \".\".join(map(str, sys.version_info[:3])))
PY
")" || sandbox_block "skill does not parse under the hermes runtime interpreter (py3.11); 3.12-only syntax? ${RUNTIME_PARSE:-runtime python missing on peer} (see tests/unit/test_py311_syntax_guard.py)"
log "runtime parse (peer): $(tail -n1 <<<"$RUNTIME_PARSE")"

run_as "$NODE_PEER_ACCOUNT" "python3 - \"\$HOME/.hermes/skills/$SKILL/SKILL.md\" \"$SKILL\" <<'PY'
import sys, pathlib
text = pathlib.Path(sys.argv[1]).read_text(encoding=\"utf-8\")
parts = text.split(\"---\n\")
assert text.startswith(\"---\n\") and len(parts) >= 3, \"missing frontmatter\"
fields = {}
for line in parts[1].splitlines():
    if \":\" in line and not line.startswith((\" \", \"\t\")):
        key, value = line.split(\":\", 1)
        fields[key.strip()] = value.strip().strip('\"')
assert fields.get(\"name\") == sys.argv[2], f\"frontmatter name != {sys.argv[2]}\"
assert len(fields.get(\"description\", \"\")) >= 10, \"description missing/too short\"
print(\"LINT-PASS name+description ok\")
PY" || sandbox_block "SKILL.md frontmatter lint failed"

hermes_lists_skill peer "$SKILL" || sandbox_block "hermes skills list does not show $SKILL on peer"
log "hermes discovery on peer: OK"

run_as "$NODE_PEER_ACCOUNT" "test -x \"\$HOME/.hermes/skills/$SKILL/scripts/scenario.sh\" || chmod +x \"\$HOME/.hermes/skills/$SKILL/scripts/scenario.sh\" 2>/dev/null; test -f \"\$HOME/.hermes/skills/$SKILL/scripts/scenario.sh\"" \
  || sandbox_block "skill ships no scripts/scenario.sh (sandbox scenario is mandatory)"

# The scenario reaches shared repo modules through AUTOPHAGY_REPO_ROOT, and different
# skills default it differently (mail resolves it skill-relative, calendar defaults to
# the ops checkout for its peer registry). Pin it to the resolved runtime root
# (release `current`, else the mirror), resolved NODE-SIDE — one root, no staged
# duplicate to drift.
# The staged copy under the peer home is, by definition, not the live mount — and every
# mutating CLI refuses to run from such a copy (skill_mount.governed_copy_refusal,
# STALE-SKILL-COPY-BLOCK). The sandbox therefore declares its own root as the live root
# so the guard sees "this copy IS the governed dir" (2026-09-03: without this, v1.1.0
# left 13 guarded skills SKILL-STALE — stage 1 blocked every one of them).
SCENARIO_OUT="$(run_as "$NODE_PEER_ACCOUNT" "$NODE_RUNTIME_ROOT_SNIPPET; RR=\$(node_runtime_root); REAL_HOME=\"\$HOME\"; IR=\"\$REAL_HOME/.hermes/interop_runtime\"; [ -d \"\$IR\" ] || { echo 'SANDBOX-HOME-BLOCK interop runtime missing' >&2; exit 90; }; SH=\$(mktemp -d) || exit 91; chmod 700 \"\$SH\" || { rm -rf \"\$SH\"; exit 91; }; trap 'rm -rf \"\$SH\"' EXIT; env -i HOME=\"\$SH\" PATH=/usr/bin:/bin \"\$(printf '%s=%s' AUTOPHAGY_REPO_ROOT \"\$RR\")\" INTEROP_RUNTIME=\"\$IR\" AUTOPHAGY_SKILL_LIVE_ROOT=\"\$REAL_HOME/.hermes/skills\" AUTOPHAGY_DEMO_SECRET=\"DUMMY-w18-sandbox-\$(date +%s)\" bash \"\$REAL_HOME/.hermes/skills/$SKILL/scripts/scenario.sh\"")" \
  || sandbox_block "scenario failed under dummy secrets"
grep -q "SCENARIO-PASS" <<<"$SCENARIO_OUT" || sandbox_block "scenario output missing SCENARIO-PASS marker"
log "scenario: $(tail -n1 <<<"$SCENARIO_OUT")"

run_as "$NODE_PEER_ACCOUNT" "if [ -r $NODE_AGENT_HOME/.env.secrets ]; then echo ISOLATION-FAIL; exit 1; else echo 'ISOLATION-OK peer cannot read agent secrets'; fi" \
  || sandbox_block "isolation probe failed: peer could read agent secrets"
log "sandbox PASS (lint + discovery + dummy-secret scenario + isolation)"
[[ "$SANDBOX_ONLY" == 1 ]] && { cleanup_peer_staging; exit 0; }

# ---------- stage 2: deterministic, hash-bound review on agent ----------
log "stage 2/4 REVIEW (agent, deterministic hash-bound verdict)"
run_as "$NODE_AGENT_ACCOUNT" "umask 077; mkdir -p \"\$HOME/.hermes/skill-gate\" && cat > \"\$HOME/.hermes/skill-gate/skill_review.py\"" \
  < "$REPO_ROOT/automation/skill_review.py" || die "REVIEW-BLOCK: review tool staging failed" 5
run_as "$NODE_AGENT_ACCOUNT" "chmod 600 \"\$HOME/.hermes/skill-gate/skill_review.py\"" || die "REVIEW-BLOCK: review tool permissions failed" 5
stage_review_source "$SRC_DIR" "$SKILL" "$DIGEST" || { cleanup_review_staging "$SKILL"; die "REVIEW-BLOCK: review source staging failed" 5; }
save_review_scenario "$SCENARIO_OUT" "$SKILL" "$DIGEST" || { cleanup_review_staging "$SKILL"; die "REVIEW-BLOCK: sandbox scenario evidence staging failed" 5; }
REVIEWED=0
review review --skill "$SKILL" --skill-dir "\$HOME/.hermes/skill-gate/review-staging/$SKILL/$DIGEST/$SKILL" \
  --hash "$DIGEST" --scenario-output-file "\$HOME/.hermes/skill-gate/review-staging/$SKILL/$DIGEST/scenario.out" >&2 || REVIEWED=$?
if [[ "$REVIEWED" != 0 ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  die "REVIEW-BLOCK: deterministic security review failed" 5
fi
log "review PASS (recorded hash-bound verdict)"

# ---------- stage 3: request, independent peer attestation, and owner approval (preceded by ops-checkout ff-only sync) ----------
log "stage 3/4 REQUEST + PEER-ATTEST + APPROVAL (#approvals)"
# Resolve the operator's deploy approvals channel pin once (fail-open to empty; once the
# request is bound, an empty value is filled from the gate's pending record below).
DEPLOY_APPROVALS_CHANNEL_ID="$(run_as "$NODE_AGENT_ACCOUNT" "python3 -c 'import json,pathlib; print(json.loads(pathlib.Path(\"~/.hermes/interop/config.json\").expanduser().read_text()).get(\"deploy_approvals_channel_id\") or \"\")'" 2>/dev/null || true)"

run_as "$NODE_AGENT_ACCOUNT" "umask 077; mkdir -p \"\$HOME/.hermes/skill-gate\" && cat > \"\$HOME/.hermes/skill-gate/skill_gate.py\"" \
  < "$REPO_ROOT/automation/skill_gate.py"
for helper in "${GATE_HELPERS[@]}"; do
  run_as "$NODE_AGENT_ACCOUNT" "umask 077; mkdir -p \"\$HOME/.hermes/interop_runtime/automation\" && cat > \"\$HOME/.hermes/interop_runtime/automation/$helper\"" \
    < "$REPO_ROOT/automation/$helper"
done
for helper in "${GATE_INTEROP_HELPERS[@]}"; do
  run_as "$NODE_AGENT_ACCOUNT" "umask 077; mkdir -p \"\$HOME/.hermes/interop_runtime/automation/interop\" && cat > \"\$HOME/.hermes/interop_runtime/automation/$helper\"" \
    < "$REPO_ROOT/automation/$helper"
done
GATE_STAGED_PATHS="\"\$HOME/.hermes/skill-gate/skill_gate.py\""
for helper in "${GATE_HELPERS[@]}" "${GATE_INTEROP_HELPERS[@]}"; do
  GATE_STAGED_PATHS+=" \"\$HOME/.hermes/interop_runtime/automation/$helper\""
done
run_as "$NODE_AGENT_ACCOUNT" "chmod 600 $GATE_STAGED_PATHS"

# Refresh notices only on the agent; peer attestation still hashes the skill tree.
for helper in "${OWNER_NOTICE_HELPERS[@]}"; do
  run_as "$NODE_AGENT_ACCOUNT" "umask 077; mkdir -p \"\$HOME/.hermes/interop_runtime/automation/interop\" && cat > \"\$HOME/.hermes/interop_runtime/automation/$helper\" && chmod 600 \"\$HOME/.hermes/interop_runtime/automation/$helper\"" \
    < "$REPO_ROOT/automation/$helper"
done

if [[ -n "$PROVENANCE_FILE" ]]; then
  PROVENANCE_REMOTE="\$HOME/.hermes/skill-gate/deploy-provenance-$SKILL-$DIGEST.json"
  run_as "$NODE_AGENT_ACCOUNT" "umask 077; cat > \"$PROVENANCE_REMOTE\"" < "$PROVENANCE_FILE" \
    || die "PROVENANCE-BLOCK: provenance staging failed"
  PROVENANCE_REQUEST_ARGS=(--provenance-file "$PROVENANCE_REMOTE")
fi

if [[ "$RELEASE_APPROVAL" == 1 ]]; then
  # VA-2: per-skill ✅ 만 릴리스 승인으로 대체한다 — SANDBOX·REVIEW·peer attestation 은
  # 위·아래 단계 그대로다. 인가 판정은 staged gate 의 release-authorize 가 소유한다.
  [[ "$PERSONAL" == 0 && "$ACTIVATE_MANAGED" == 0 ]] \
    || die "--release-approval applies to repository skill deploys only" 2
  [[ "${E2E_TEST_MODE:-}" != "1" ]] \
    || die "--release-approval has no injected-approval variant" 2
  AUTH_RC=0
  AUTH_OUT="$(gate "" release-authorize --skill "$SKILL" --hash "$DIGEST")" || AUTH_RC=$?
  case "$AUTH_RC" in
    0) ;;
    8)
      cleanup_peer_staging
      cleanup_review_staging "$SKILL"
      log "owner CANCELLED the release — NOT deploying $SKILL"
      exit 9
      ;;
    4) die "RELEASE-APPROVAL-BLOCK: the release approval does not cover $SKILL@$DIGEST — 릴리스 이후 바뀐 스킬은 옆 승인으로 못 탄다" 4 ;;
    2) die "RELEASE-APPROVAL-BLOCK: no usable release approval record — run automation/release.sh first" 4 ;;
    *)
      cleanup_peer_staging
      cleanup_review_staging "$SKILL"
      log "release approval pending or unverifiable — NOT deploying (rc=$AUTH_RC)"
      exit 1
      ;;
  esac
  MESSAGE_ID="${AUTH_OUT%%:*}"
  DEPLOY_NONCE="${AUTH_OUT##*:}"
  [[ "$DEPLOY_NONCE" =~ ^[0-9a-f]{32}$ ]] \
    || die "RELEASE-APPROVAL-BLOCK: release record carries no usable nonce"
  log "release approval covers $SKILL@${DIGEST:0:12} — per-skill owner ✅ replaced (VA-2)"
elif [[ -n "${APPROVAL_MESSAGE_ID:-}" ]]; then
  MESSAGE_ID="$APPROVAL_MESSAGE_ID"
  DEPLOY_NONCE="${DEPLOY_NONCE:-}"
  [[ "$DEPLOY_NONCE" =~ ^[0-9a-f]{32}$ ]] || die "APPROVAL_MESSAGE_ID requires a valid DEPLOY_NONCE"
else
  FRESH_FLAG=""; [[ "$FRESH" == 1 ]] && FRESH_FLAG="--fresh"
  REQUEST_JSON="$(gate "SKILL_PROPOSAL_SOURCE='${SKILL_PROPOSAL_SOURCE:-manual}'" \
    request --skill "$SKILL" --hash "$DIGEST" --json --peer-attest-mode "$NODE_PEER_ATTEST_MODE" $FRESH_FLAG "${PROVENANCE_REQUEST_ARGS[@]}")" || { rc=$?; [[ $rc == 3 ]] && die "weekly auto-proposal rate limit" 3; [[ $rc == 6 ]] && die "approval lifecycle refusal (existing live request preserved; gate stderr carries reason=)" 6; die "approval request failed"; }
  mapfile -t REQUEST_FIELDS < <(python3 -c 'import json, sys; row = json.load(sys.stdin); print(row["message_id"]); print(row["deploy_nonce"])' <<<"$REQUEST_JSON")
  [[ "${#REQUEST_FIELDS[@]}" == 2 ]] || die "approval request did not return message id and deploy nonce"
  MESSAGE_ID="${REQUEST_FIELDS[0]}"
  DEPLOY_NONCE="${REQUEST_FIELDS[1]}"
fi
# No deploy_approvals_channel_id pin: the gate resolved the surface itself and stored the
# channel it posted to on the bound record. Signed mode signs (and the gate verifies) that
# exact channel, so read it from there rather than leave peer_attest without --channel-id.
if [[ -z "$DEPLOY_APPROVALS_CHANNEL_ID" ]]; then
  BOUND_RECORD="$SKILL"; [[ "$RELEASE_APPROVAL" == 1 ]] && BOUND_RECORD="release"
  DEPLOY_APPROVALS_CHANNEL_ID="$(run_as "$NODE_AGENT_ACCOUNT" "python3 -c 'import json,pathlib; print(json.loads(pathlib.Path(\"~/.hermes/skill-gate/pending/$BOUND_RECORD.json\").expanduser().read_text()).get(\"channel_id\") or \"\")'" 2>/dev/null || true)"
fi
log "approval request posted; peer attestation required"
PEER_ATTESTED=0
PEER_ATTEST_BLOB="$(peer_attest "$SKILL" "$DIGEST" "$MESSAGE_ID" "$DEPLOY_NONCE" "$DEPLOY_APPROVALS_CHANNEL_ID")" || PEER_ATTESTED=$?
if [[ "$PEER_ATTESTED" != 0 ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  die "PEER-ATTEST-BLOCK: independent peer review failed" 5
fi
if [[ "$NODE_PEER_ATTEST_MODE" == "discord" ]]; then
  printf '%s\n' "$PEER_ATTEST_BLOB" >&2
  PEER_ATTEST_BLOB=""
  log "peer attestation PASS (posted by peer bot)"
else
  log "peer attestation PASS (signed stdout record captured)"
fi
[[ "$REQUEST_ONLY" == 1 ]] && { cleanup_peer_staging; cleanup_review_staging "$SKILL"; log "request-only mode: attested but not mounting"; echo "$MESSAGE_ID"; exit 0; }

if [[ "${E2E_TEST_MODE:-}" == "1" ]]; then
  log "E2E regression path: signed injected approval (production gateway refuses this mode)"
  run_as "$NODE_AGENT_ACCOUNT" "umask 077; openssl rand -hex 32 > \"\$HOME/.hermes/skill-gate/e2e.secret\""
  E2E_ENV='E2E_TEST_MODE=1 INTEROP_E2E_SECRET=$(cat "$HOME/.hermes/skill-gate/e2e.secret")'
  gate "$E2E_ENV" sign --skill "$SKILL" --hash "$DIGEST" --message-id "$MESSAGE_ID" \
    --out "\$HOME/.hermes/skill-gate/injected-approval.json" >&2
fi
APPROVED=0
if [[ "$RELEASE_APPROVAL" == 1 ]]; then
  release_authorize_with_refresh "$DIGEST" || APPROVED=$?
else
  check_with_attestation_refresh "$DIGEST" || APPROVED=$?
fi

# The owner answered, and the answer was no. Tested before the generic case so a
# cancellation is never reported as a missing reply: exit 8 comes from the gate's
# DENIED_EXIT and means ⛔ was on the request. Nothing here retries — re-attesting a
# deploy the owner stopped spends a Discord round-trip on nothing and makes the log
# read as though the pipeline were still pushing after they said stop.
if [[ "$APPROVED" == 8 ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  cleanup_e2e_injection
  log "owner CANCELLED this deploy — NOT mounting, and not retrying (message $MESSAGE_ID)"
  # NOT 8: that already means "another execution holds this skill's lease", which a
  # caller must RETRY. A cancellation must be RETIRED. One number for both would let a
  # watcher destroy a live approval that had merely collided with a concurrent deploy.
  exit 9
fi

if [[ "$APPROVED" == 2 ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  cleanup_e2e_injection
  log "approval gate CONFIGURATION INVALID — NOT mounting (message $MESSAGE_ID stays pending)"
  exit 4
fi

if [[ "$APPROVED" != 0 ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  cleanup_e2e_injection
  log "approval ABSENT or INVALID — NOT mounting (message $MESSAGE_ID stays pending for the owner)"
  exit 1
fi
log "approval GRANTED (logged to approvals.jsonl)"

if [[ "$APPROVE_ONLY" == 1 ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  cleanup_e2e_injection
  log "approve-only mode: approval verified; NOT mounting"
  printf '%s:%s\n' "$MESSAGE_ID" "$DEPLOY_NONCE"
  exit 0
fi

# ---------- stage 4: recheck reviewed current hash, then mount its artifact ----------
if [[ "$PERSONAL" == 1 ]]; then
  personal_provenance_check "$SRC_DIR" "$PERSONAL_HEAD_SHA" >/dev/null || exit 4
fi
CURRENT_DIGEST="$(skill_digest "$SRC_DIR")"
if [[ "$CURRENT_DIGEST" != "$DIGEST" ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  die "REVIEW-BLOCK: source changed after review" 5
fi
REVIEW_CURRENT=0
review check --skill "$SKILL" --hash "$CURRENT_DIGEST" >&2 || REVIEW_CURRENT=$?
if [[ "$REVIEW_CURRENT" != 0 ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  die "REVIEW-BLOCK: PASS verdict missing for current skill hash" 5
fi
MOUNT_APPROVED=0
if [[ "$RELEASE_APPROVAL" == 1 ]]; then
  release_authorize_with_refresh "$CURRENT_DIGEST" || MOUNT_APPROVED=$?
else
  check_with_attestation_refresh "$CURRENT_DIGEST" || MOUNT_APPROVED=$?
fi
if [[ "$MOUNT_APPROVED" == 2 ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  cleanup_e2e_injection
  die "MOUNT-BLOCK: approval gate configuration became invalid" 4
fi
if [[ "$MOUNT_APPROVED" != 0 ]]; then
  cleanup_peer_staging
  cleanup_review_staging "$SKILL"
  cleanup_e2e_injection
  die "MOUNT-BLOCK: owner approval or peer attestation no longer valid" 1
fi
log "stage 4/4 MOUNT (root-owned immutable skill store)"
if [[ "$ACTIVATE_MANAGED" == 1 ]]; then
  install_managed_skill "$SRC_DIR" "$MANAGED_PUBLISHER" "$SKILL" "$DIGEST" || die "MANAGED-BLOCK: privileged managed artifact install failed" 5
else
  install_reviewed_skill "$SRC_DIR" "$SKILL" "$DIGEST" || die "REVIEW-BLOCK: privileged reviewed artifact install failed" 5
fi
# Installation realizes the owner's decision. Retire the pending record before
# post-install checks can abort: failure after the live link exists must not leave
# a deploy replayable. Compare-and-swap inside the gate: a record that no longer
# binds this (skill, hash, message id) is left alone. A failure here is LOUD but
# NEVER fatal — the reviewed artifact is already live, so rolling the mount back
# over leftover bookkeeping would be the worse outcome.
if [[ "$RELEASE_APPROVAL" == 1 ]]; then
  log "release-approval deploy: no per-skill pending record to retire — 릴리스 레코드는 스킬별로 소진되지 않는다"
else
  CONSUMED=0
  gate "" consume --skill "$SKILL" --hash "$DIGEST" --message-id "$MESSAGE_ID" >&2 || CONSUMED=$?
  [[ "$CONSUMED" == 0 ]] || log "CONSUME-WARN: pending approval record NOT retired for $SKILL (rc=$CONSUMED); mount stands. Retire it with: skill_gate.py abandon --skill $SKILL --hash $DIGEST --message-id $MESSAGE_ID --reason <text>"
fi
hermes_lists_skill agent "$SKILL" || die "mounted but hermes skills list does not show $SKILL on agent"
INVOKE_OUT="$(run_as "$NODE_AGENT_ACCOUNT" "REAL_HOME=\"\$HOME\"; IR=\"\$REAL_HOME/.hermes/interop_runtime\"; [ -d \"\$IR\" ] || { echo 'SANDBOX-HOME-BLOCK interop runtime missing' >&2; exit 90; }; SH=\$(mktemp -d) || exit 91; chmod 700 \"\$SH\" || { rm -rf \"\$SH\"; exit 91; }; trap 'rm -rf \"\$SH\"' EXIT; env -i HOME=\"\$SH\" PATH=/usr/bin:/bin INTEROP_RUNTIME=\"\$IR\" AUTOPHAGY_DEMO_SECRET=DUMMY-w18-agent-invoke bash \"$STORE_ROOT/live/$SKILL/scripts/scenario.sh\"")" \
  || die "post-mount invoke smoke failed on agent"
log "invoke: $(head -n1 <<<"$INVOKE_OUT")"
release_execution_lock
cleanup_peer_staging
cleanup_review_staging "$SKILL"
cleanup_e2e_injection
log "DEPLOYED $SKILL (sandbox→review→approval→mount complete; sandbox copy cleaned)"
exit 0

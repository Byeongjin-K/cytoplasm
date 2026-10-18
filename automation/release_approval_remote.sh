#!/usr/bin/env bash
# Workstation-to-agent transport for the release approval producer.
#
# The signing key belongs on the workstation, while the Discord bot credential
# and the authoritative skill-gate ledger belong to the node's agent account.
# Stage the exact committed automation tree, then execute request/decision at
# that authority boundary.  No credential is copied back to the workstation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
eval "$(python3 "$SCRIPT_DIR/node_config_sh.py" --print-env)"

ssh_bin="${RELEASE_APPROVAL_SSH:-ssh}"
host="${RELEASE_APPROVAL_HOST:-${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}}"
account="${RELEASE_APPROVAL_ACCOUNT:-$NODE_AGENT_ACCOUNT}"
agent_home="${RELEASE_APPROVAL_HOME:-$NODE_AGENT_HOME}"
head="$(git -C "$REPO_ROOT" rev-parse HEAD)"
stage="$agent_home/.hermes/release-approval-runtime/$head"
archive_sha="$(
  git -C "$REPO_ROOT" archive "$head" automation configs/node.example.toml | sha256sum | cut -d' ' -f1
)"

remote() { # remote <script>; stdin is deliberately forwarded
  local script="$1"
  if [[ -n "$host" ]]; then
    "$ssh_bin" "$host" \
      "sudo -n -u $account -H bash -c $(printf '%q' "$script")"
  else
    sudo -n -u "$account" -H bash -c "$script"
  fi
}

marker="$stage/.archive-sha256"
if ! remote "test -f '$marker' && test \"\$(cat '$marker')\" = '$archive_sha'" </dev/null
then
  incoming="${stage}.incoming.$$"
  git -C "$REPO_ROOT" archive "$head" automation configs/node.example.toml \
    | remote "set -eu; umask 077; rm -rf '$incoming'; mkdir -p '$incoming'; tar -xf - -C '$incoming'; printf '%s\n' '$archive_sha' > '$incoming/.archive-sha256'; rm -rf '$stage'; mv '$incoming' '$stage'"
fi

# 실행 모듈은 호출자가 고른다 — 같은 권한 경계(agent 시크릿 + 스테이징된 커밋 트리)를
# 쓰는 생산자가 둘 이상이라 전송로를 복사하지 않기 위한 유일한 이음매다.
module="${RELEASE_APPROVAL_MODULE:-automation.release_approval}"
prefix="set -a; . '$agent_home/.env.secrets'; set +a; cd '$stage'; PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='$stage' python3 -m $module"
if [[ "${1:-}" == "request" && "${2:-}" == "--plan-file" && -n "${3:-}" && $# == 3 ]]
then
  # Python reopening /dev/stdin after ssh→sudo crosses an fd owned by the
  # parent SSH process and can fail EACCES.  Let the agent read fd 0 once into
  # its own 0600 file, then give pathlib a normal agent-owned path.
  remote "set -eu; umask 077; plan=\$(mktemp '$agent_home/.hermes/release-plan.XXXXXX'); trap 'rm -f \"\$plan\"' EXIT; cat > \"\$plan\"; $prefix request --plan-file \"\$plan\"" < "$3"
  exit $?
fi

quoted=""
for argument in "$@"; do
  printf -v one '%q' "$argument"
  quoted+=" $one"
done
remote "$prefix$quoted" </dev/null

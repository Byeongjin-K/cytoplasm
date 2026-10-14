#!/usr/bin/env bash
# RC-2 owner-run installer for the healthcheck SSH forced-command wrapper.
#
# Command discovery remains in healthcheck_probe_wrapper.sh.  This boundary only stages,
# validates, atomically installs, and reads the bytes back.  Run it as the node operator
# who owns ~/.local/libexec; it intentionally uses neither sudo nor a remote transport.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly GENERATOR="${HEALTHCHECK_WRAPPER_GENERATOR:-$SCRIPT_DIR/healthcheck_probe_wrapper.sh}"
readonly TARGET="${HEALTHCHECK_WRAPPER_PATH:-$HOME/.local/libexec/autophagy-healthcheck-probe}"

die() {
  printf 'WRAPPER-PROVISION-BLOCK: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'usage: provision-healthcheck-probe.sh [--from-file <wrapper>] [node]\n'
}

# --from-file: 릴리스 트리가 없는 노드(RAG)는 주 노드 릴리스가 `--print <node>` 로 생성한 래퍼를
# 받아 놓는다. 검사·원자 교체·read-back 은 생성 경로와 같다.
from_file=""
if [[ "${1:-}" == "--from-file" ]]; then
  (($# >= 2)) || { usage >&2; exit 2; }
  from_file="$2"
  shift 2
fi
if (($# > 1)); then
  usage >&2
  exit 2
fi
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

node="${1:-}"
staged="$(mktemp)" || die "mktemp failed"
target_tmp=""
cleanup() {
  rm -f -- "$staged"
  [[ -z "$target_tmp" ]] || rm -f -- "$target_tmp"
}
trap cleanup EXIT

if [[ -n "$from_file" ]]; then
  [[ -n "$node" ]] || die "--from-file needs the node name"
  cp -- "$from_file" "$staged" || die "cannot read $from_file"
  grep -qx "# wrapper-node: $node" "$staged" || die "$from_file was not generated for node $node"
  grep -Eq '^# wrapper-inputs: [0-9a-f]{64}$' "$staged" || die "$from_file carries no wrapper-inputs header"
else
  generator_args=(--print)
  [[ -z "$node" ]] || generator_args+=("$node")
  bash "$GENERATOR" "${generator_args[@]}" > "$staged" \
    || die "wrapper generation failed"
fi
bash -n "$staged" || die "generated wrapper is not valid bash"

want="$(sha256sum "$staged" | cut -d' ' -f1)"
if [[ -f "$TARGET" ]] \
  && cmp -s "$staged" "$TARGET" \
  && [[ "$(stat -c '%a' "$TARGET" 2>/dev/null)" == "755" ]]; then
  printf 'WRAPPER-UNCHANGED %s sha256=%s\n' "$TARGET" "$want"
  exit 0
fi

install -d -m 0755 "$(dirname "$TARGET")"
target_tmp="${TARGET}.tmp.$$"
install -m 0755 "$staged" "$target_tmp" || die "could not stage $TARGET"
mv -f -- "$target_tmp" "$TARGET" || die "could not replace $TARGET"
target_tmp=""

got="$(sha256sum "$TARGET" 2>/dev/null | cut -d' ' -f1)" \
  || die "could not read back $TARGET"
[[ "$got" == "$want" ]] \
  || die "read-back mismatch for $TARGET (want=${want:0:16} got=${got:0:16})"
[[ "$(stat -c '%a' "$TARGET" 2>/dev/null)" == "755" ]] \
  || die "installed wrapper mode is not 755: $TARGET"
printf 'WRAPPER-INSTALLED %s sha256=%s\n' "$TARGET" "$want"

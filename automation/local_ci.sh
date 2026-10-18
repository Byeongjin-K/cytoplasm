#!/usr/bin/env bash
# GitHub CI 가 PR 에서 돌리는 것과 같은 검증을 로컬에서 돌리고, 통과했을 때만 영수증을 남긴다.
#
# 왜 필요한가: 이 저장소는 private + Free 라 브랜치 보호를 쓸 수 없다 — `gh api
# repos/.../branches/main/protection` 이 403 이다. 즉 GitHub CI 는 머지를 막을 권한이 없는
# **권고**였고, 2026-08-25 에 빨간 CI(실은 결제 문제로 잡이 시작조차 못 한 것) 위에서 머지가
# 실제로 이뤄졌다. "PR 전에 CI 를 돌려라"를 산문으로 적으면 그 문장을 읽는 주체가 지키는
# 만큼만 작동하므로, 판정은 `automation/hooks/pre-push` 가 이 영수증을 보고 내린다.
#
# 영수증의 키는 commit 이 아니라 **tree** 다. amend·rebase 로 커밋 해시가 바뀌어도 내용이 같으면
# 그대로 유효하고, 내용이 한 글자라도 바뀌면 즉시 무효다. 워크플로 자신의 sha256 도 함께 담아,
# `.github/workflows/ci.yml` 이 바뀌면 그 전에 받은 영수증은 무효가 된다.
#
# 한계(알고 쓰는 것): 여기서 증명되는 것은 "이 트리가 이 기계에서 통과했다"이지 "깨끗한 호스트에서
# 통과한다"가 아니다. 그 간극은 clean-host 단계(빈 컨테이너)가 메우고, 완전히는 못 메운다.
#
# 사용:
#   automation/local_ci.sh run           # 전 단계 실행 → 통과 시 HEAD 트리의 영수증 발급
#   automation/local_ci.sh verify <sha>  # 그 sha 의 트리에 유효한 영수증이 있으면 exit 0
#
# Env:
#   LOCAL_CI_STATE_DIR         default ~/.hermes/local-ci (체크아웃 밖 — 「추적 config = 불변 시드」)
#   LOCAL_CI_CONTAINER_RUNNER  default docker (테스트 주입용 이음새)
#   LOCAL_CI_ALLOW_UNVERIFIED  훅 전용 탈출구. **샌드박스/실험 전용** — 상습 사용은 게이트를 없앤다.
set -uo pipefail

readonly SCHEMA_VERSION=1
readonly CONTAINER_IMAGE="python:3.12-slim"

log() { printf '[local-ci] %s\n' "$*"; }
die() { printf '[local-ci] %s\n' "$1" >&2; exit "${2:-1}"; }

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || die "not inside a git checkout — run this from the repository"
readonly REPO_ROOT
readonly WORKFLOW="$REPO_ROOT/.github/workflows/ci.yml"
readonly STATE_DIR="${LOCAL_CI_STATE_DIR:-$HOME/.hermes/local-ci}"
readonly CONTAINER_RUNNER="${LOCAL_CI_CONTAINER_RUNNER:-docker}"

STEP_NAMES=()
STEP_COMMANDS=()
STEP_CODES=()

#: 실행하고 결과를 영수증용으로 적재한다. 실패는 그대로 호출자에게 돌려준다 — 어떤 단계든
#: 실패하면 영수증은 만들어지지 않아야 하고, 그 판단은 cmd_run 이 소유한다.
record_step() { # record_step <name> <command...>
  local name="$1"
  shift
  log "step ${name}: $*"
  "$@"
  local code=$?
  STEP_NAMES+=("$name")
  STEP_COMMANDS+=("$*")
  STEP_CODES+=("$code")
  return "$code"
}

# 워크플로의 clean-host-install 잡과 같은 본문이다. 러너 이미지에 이미 깔려 있는 것에 가려지는
# 설치기 회귀를 잡는 것이 이 단계의 존재 이유이므로, 의존성을 미리 설치하지 않는다.
read -r -d '' CLEAN_HOST_SCRIPT <<'INNER_EOF' || true
set -euo pipefail
key="$(mktemp -d)/update-trust.pub"
python3 - "$key" <<'PY'
import base64, sys
algorithm = b"ssh-ed25519"
blob = len(algorithm).to_bytes(4, "big") + algorithm
blob += (32).to_bytes(4, "big") + bytes(range(32))
material = base64.b64encode(blob).decode()
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    stream.write(f"ssh-ed25519 {material} ci-clean-host\n")
PY
python3 -m automation.install --dry-run >/dev/null
python3 -m automation.install --profile core --update-trust-key "$key" --dry-run >/dev/null
python3 -m automation.install --dry-run --role okdori --role moodori >/dev/null
python3 -m pip install --disable-pip-version-check --quiet pytest
python3 -m pytest tests/unit/test_install_third_party_boundary.py -q
INNER_EOF

tool_version() { # tool_version <command...>
  "$@" 2>&1 | head -n1 || printf 'unavailable\n'
}

write_receipt() { # write_receipt <tree> <commit> <workflow-digest>
  mkdir -p -m 700 "$STATE_DIR" || die "cannot create the receipt directory: $STATE_DIR"
  chmod 700 "$STATE_DIR" || die "cannot secure the receipt directory: $STATE_DIR"
  local target="$STATE_DIR/$1.json"
  LOCAL_CI_TREE="$1" LOCAL_CI_COMMIT="$2" LOCAL_CI_WORKFLOW="$3" \
  LOCAL_CI_SCHEMA="$SCHEMA_VERSION" LOCAL_CI_TARGET="$target" \
  LOCAL_CI_NAMES="$(printf '%s\n' "${STEP_NAMES[@]}")" \
  LOCAL_CI_CMDS="$(printf '%s\n' "${STEP_COMMANDS[@]}")" \
  LOCAL_CI_CODES="$(printf '%s\n' "${STEP_CODES[@]}")" \
  LOCAL_CI_TOOLS="$(tool_version ruff --version); $(tool_version python3 -m pytest --version)" \
  python3 <<'PY' || die "cannot write the receipt"
import json, os, time
from pathlib import Path

names = os.environ["LOCAL_CI_NAMES"].splitlines()
commands = os.environ["LOCAL_CI_CMDS"].splitlines()
codes = [int(value) for value in os.environ["LOCAL_CI_CODES"].splitlines()]
target = Path(os.environ["LOCAL_CI_TARGET"])
payload = {
    "schema": int(os.environ["LOCAL_CI_SCHEMA"]),
    "tree": os.environ["LOCAL_CI_TREE"],
    "commit": os.environ["LOCAL_CI_COMMIT"],
    "workflow_sha256": os.environ["LOCAL_CI_WORKFLOW"],
    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "tools": os.environ["LOCAL_CI_TOOLS"],
    "steps": [
        {"name": name, "command": command, "rc": code}
        for name, command, code in zip(names, commands, codes)
    ],
}
target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
target.chmod(0o600)
PY
  log "receipt $target"
}

cmd_run() {
  [[ -f "$WORKFLOW" ]] || die "no workflow to mirror at $WORKFLOW"
  local dirty
  # 더러운 트리에서 발급하면 영수증이 "이 트리가 통과했다"고 거짓말한다 — 검사한 것은 다른 내용이다.
  # 예외는 `.omo/senpi-task/` 뿐이다: 에이전트 하네스가 매 턴 다시 쓰는 세션 장부이고 어떤 검사도
  # 읽지 않아 결과를 바꿀 수 없다. 그것까지 막으면 이 환경에서는 게이트를 통과할 방법이 없어진다.
  dirty="$(git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=no \
    -- . ':(exclude).omo/senpi-task')"
  [[ -z "$dirty" ]] || die "tracked files are modified; commit first, then run: automation/local_ci.sh run"

  local tree commit workflow_digest
  tree="$(git -C "$REPO_ROOT" rev-parse 'HEAD^{tree}')" || die "cannot resolve HEAD"
  commit="$(git -C "$REPO_ROOT" rev-parse HEAD)" || die "cannot resolve HEAD"
  workflow_digest="$(sha256sum -- "$WORKFLOW" | cut -d' ' -f1)"

  cd "$REPO_ROOT" || die "cannot enter $REPO_ROOT"
  command -v bwrap >/dev/null \
    || die "bubblewrap is required; install it before running local CI (Ubuntu: sudo apt-get install bubblewrap)"
  record_step lint ruff check . --exclude skills/mail/vendor \
    || die "lint failed — no receipt written"
  record_step unit-tests python3 -m pytest tests/unit -q \
    || die "unit tests failed — no receipt written"
  record_step public-gate python3 -m automation.public_gate range origin/main HEAD --text-env PR_TEXT \
    || die "public leak gate failed — no receipt written"
  record_step clean-host "$CONTAINER_RUNNER" run --rm \
    -e PYTHONDONTWRITEBYTECODE=1 -v "$REPO_ROOT:/w:ro" -w /w \
    "$CONTAINER_IMAGE" bash -c "$CLEAN_HOST_SCRIPT" \
    || die "clean-host install check failed — no receipt written"

  write_receipt "$tree" "$commit" "$workflow_digest"
  log "PASS $tree (commit $commit)"
}

cmd_verify() { # cmd_verify <sha>
  local sha="${1:-}"
  [[ -n "$sha" ]] || die "usage: local_ci.sh verify <sha>"
  local tree workflow_digest receipt
  tree="$(git -C "$REPO_ROOT" rev-parse "$sha^{tree}" 2>/dev/null)" \
    || die "cannot resolve a tree for $sha"
  workflow_digest="$(git -C "$REPO_ROOT" show "$sha:.github/workflows/ci.yml" 2>/dev/null \
    | sha256sum | cut -d' ' -f1)"
  receipt="$STATE_DIR/$tree.json"
  [[ -f "$receipt" ]] \
    || die "no local CI receipt for tree $tree — run: automation/local_ci.sh run"

  LOCAL_CI_RECEIPT="$receipt" LOCAL_CI_TREE="$tree" LOCAL_CI_WORKFLOW="$workflow_digest" \
  LOCAL_CI_SCHEMA="$SCHEMA_VERSION" python3 <<'PY'
import json, os, sys
from pathlib import Path

receipt = Path(os.environ["LOCAL_CI_RECEIPT"])
remedy = "run: automation/local_ci.sh run"
try:
    payload = json.loads(receipt.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    sys.exit(f"[local-ci] unreadable receipt ({error.__class__.__name__}) — {remedy}")
if payload.get("schema") != int(os.environ["LOCAL_CI_SCHEMA"]):
    sys.exit(f"[local-ci] receipt schema is not understood — {remedy}")
if payload.get("tree") != os.environ["LOCAL_CI_TREE"]:
    sys.exit(f"[local-ci] receipt belongs to another tree — {remedy}")
if payload.get("workflow_sha256") != os.environ["LOCAL_CI_WORKFLOW"]:
    sys.exit(f"[local-ci] the workflow changed after this receipt — {remedy}")
steps = payload.get("steps")
if not isinstance(steps, list) or not steps:
    sys.exit(f"[local-ci] receipt records no step — {remedy}")
failed = [step.get("name") for step in steps if step.get("rc") != 0]
if failed:
    sys.exit(f"[local-ci] receipt records failing step(s) {failed} — {remedy}")
PY
}

case "${1:-}" in
  run) shift; cmd_run "$@" ;;
  verify) shift; cmd_verify "$@" ;;
  *) die "usage: local_ci.sh run | local_ci.sh verify <sha>" 2 ;;
esac

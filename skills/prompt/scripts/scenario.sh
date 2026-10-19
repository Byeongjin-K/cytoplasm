#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[[ -n "$secret" ]] || fail "AUTOPHAGY_DEMO_SECRET is not set"
[[ "$secret" == DUMMY-* ]] || fail "secret does not carry the DUMMY- prefix"
if [[ "$secret" == *sk-* || "$secret" == *ghp_* || "$secret" == *"Bot "* ]]; then
  fail "secret matches a real-token shape"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

# The node mirror only when this account can read it: the operator on the primary node
# sees it but cannot enter it, and would otherwise fail on its prompt library.
if [[ -r /srv/autophagy-agents/prompts/README.md ]]; then
  repo_root=/srv/autophagy-agents
else
  repo_root="$(cd "$script_dir/../../.." && pwd)"
fi
export PROMPT_REPO_ROOT="$repo_root"
export PROMPT_OVERLAY_ROOT="$work/overlay"
export PROMPT_PRIVATE_ROOT="$work/private"
export PROMPT_MEETING_SCRIPTS="$repo_root/skills/meeting/scripts"

cli() { python3 -I "$script_dir/prompt_cli.py" "$@"; }
python3 -I -c 'import sys; assert "" not in sys.path' || fail "python isolation"

printf 'neutral asset line one\nneutral asset line two\n' > "$work/entry-v1.md"
first="$(cli add --id scenario-entry --category task --purpose "scenario asset" --model any --tags "sandbox,prompt" --body-file "$work/entry-v1.md")"
printf '%s' "$first" | grep -q 'version=1' || fail "first add version"
cli search scenario-entry > "$work/search.out"
[[ "$(wc -l < "$work/search.out")" -eq 1 ]] || fail "search count"
cli get scenario-entry --write-body "$work/get-v1.md" > "$work/get-v1.out"
cmp -s "$work/entry-v1.md" "$work/get-v1.md" || fail "get v1 roundtrip"
[[ "$(stat -c %a "$work/get-v1.md")" == "600" ]] || fail "get body mode"

printf 'neutral replacement line one\nneutral replacement line two\n' > "$work/entry-v2.md"
second="$(cli add --id scenario-entry --category task --purpose "scenario asset" --model any --tags "sandbox,prompt" --body-file "$work/entry-v2.md")"
printf '%s' "$second" | grep -q 'version=2' || fail "version increment"
cli get scenario-entry > "$work/latest.out"
grep -q 'version=2' "$work/latest.out" || fail "latest version"
cli get scenario-entry --version 1 --write-body "$work/get-version-1.md" > /dev/null
cmp -s "$work/entry-v1.md" "$work/get-version-1.md" || fail "versioned get"

canary="prompt-canary-$(date +%s)-$$"
printf '특허 patent 기밀\n' > "$work/classified-body.md"
printf '\n%s\n' "$canary" >> "$work/classified-body.md"
classified="$(cli add --id classified-entry --category task --purpose "private asset" --model any --tags "sandbox,private" --body-file "$work/classified-body.md")"
printf '%s' "$classified" | grep -q 'body_ref=inline' || fail "keyword inline"
stub="$PROMPT_OVERLAY_ROOT/classified-entry/v1.md"
[[ -f "$stub" ]] || fail "metadata stub absent"
python3 -I - "$stub" "$script_dir" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])  # scripts dir: the live mount has no `skills` package above it
import prompt_schema

metadata, body = prompt_schema.parse_entry(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert metadata.body_ref == "inline"
assert "특허 patent 기밀" in body
PY
[[ "$(stat -c %a "$(dirname "$stub")")" == "700" ]] || fail "overlay root mode"
[[ "$(stat -c %a "$stub")" == "600" ]] || fail "overlay file mode"
cli get classified-entry --write-body "$work/classified-get.md" > "$work/classified-get.out"
cmp -s "$work/classified-body.md" "$work/classified-get.md" || fail "private get"

legacy_before="$(sha256sum "$repo_root/prompts/meeting-extraction-v1.md" | cut -d' ' -f1)"
cli get meeting-extraction --version 1 > "$work/legacy.out"
legacy_after="$(sha256sum "$repo_root/prompts/meeting-extraction-v1.md" | cut -d' ' -f1)"
[[ "$legacy_before" == "$legacy_after" ]] || fail "legacy modified"

printf 'SCENARIO-PASS versions=2 keywords=inline legacy_read_only=true secret_len=%s account=%s\n' \
  "${#secret}" "$(whoami)"

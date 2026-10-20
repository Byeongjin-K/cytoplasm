#!/usr/bin/env bash
# Pin the Hermes gateway's import root to the current release generation (RCB todo 22).
#
# Runs as an ExecStartPre of hermes-gateway.service (gateway-dropin.conf) on every start, so a
# restart always imports the plugin body and the external-effect gate from one generation:
#   root = AUTOPHAGY_RUNTIME_ROOT, else ${PIN_RELEASE_POINTER:-/srv/autophagy-agent-current}
#          when present, else ${PIN_MIRROR_ROOT:-/srv/autophagy-agents} (PIN-IMPORT-ROOT-WARN)
#   gen  = readlink -f root; every file of the gate closure must exist in it, or exit 78
#          (systemd then keeps the gateway down and retries; the old link stays as it was)
#   farm = ~/.hermes/autophagy-import.d/<gen name>/automation/<entry> -> gen/automation/<entry>
#          for every entry except hermes_compat and __pycache__: the compat carrier's
#          automation.hermes_compat must keep coming from the carrier, never from the release.
#   link = ~/.hermes/autophagy-import -> autophagy-import.d/<farm>, flipped with one rename
#          only after the farm is complete. A farm of the same name that no longer matches the
#          tree (a mirror that moved) is kept and a new farm <name>~<UTC>-<pid> is built.
# Earlier farms are never removed. Success prints `PIN-IMPORT-ROOT-OK <farm>`.
# Env: PIN_LOCK_WAIT seconds to wait for the store lock (default 30).
set -euo pipefail

block() { printf 'PIN-IMPORT-ROOT-BLOCK: %s\n' "$*" >&2; exit 78; }

home="${HOME:?HOME is not set}"
root="${AUTOPHAGY_RUNTIME_ROOT:-}"
if [[ -z "$root" ]]; then
  root="${PIN_RELEASE_POINTER:-/srv/autophagy-agent-current}"
  if [[ ! -e "$root" ]]; then
    mirror="${PIN_MIRROR_ROOT:-/srv/autophagy-agents}"
    printf 'PIN-IMPORT-ROOT-WARN: %s is absent; pinning the mirror %s\n' "$root" "$mirror" >&2
    root="$mirror"
  fi
fi
gen="$(readlink -f -- "$root" 2>/dev/null || true)"
[[ -n "$gen" ]] || gen="$root"

# Keep this list equal to automation/interop/gateway_preflight.py REQUIRED (tests pin both).
for relative in automation/__init__.py automation/interop/external_effect_gate.py \
  automation/interop/hermes_plugin/__init__.py automation/gateway_generation.py \
  automation/interop/policy_paths.py automation/interop/plugin_shim/__init__.py; do
  [[ -f "$gen/$relative" ]] || block "$gen/$relative"
done
name="${gen##*/}"
[[ -n "$name" && "$name" != . && "$name" != .. ]] || block "$gen has no usable name"

store="$home/.hermes/autophagy-import.d"
link="$home/.hermes/autophagy-import"
if [[ -e "$link" && ! -L "$link" ]]; then
  block "$link is not a link; refusing to replace it"
fi
mkdir -p -- "$store" || block "cannot create $store"
exec 9>>"$store/.lock" || block "cannot open $store/.lock"
flock -w "${PIN_LOCK_WAIT:-30}" 9 || block "$store/.lock is busy"

expected() {
  local entry
  for entry in "$gen"/automation/*; do
    [[ -e "$entry" ]] || continue
    case "${entry##*/}" in hermes_compat|__pycache__) continue ;; esac
    printf '%s\n' "${entry##*/}"
  done
}

farm_matches() { # <farm dir>
  local entry got
  [[ -d "$1/automation" && ! -L "$1/automation" ]] || return 1
  got="$(cd -- "$1/automation" && for entry in *; do
    [[ -L "$entry" && "$(readlink -- "$entry")" == "$gen/automation/$entry" ]] || { echo "!$entry"; continue; }
    echo "$entry"
  done)"
  [[ "$got" == "$(expected)" ]]
}

target=""
if [[ -e "$store/$name" || -L "$store/$name" ]]; then
  if farm_matches "$store/$name"; then target="$name"; else target="$name~$(date -u +%Y%m%dT%H%M%SZ)-$$"; fi
else
  target="$name"
fi

if [[ ! -e "$store/$target" ]]; then
  building="$(mktemp -d "$store/.building.XXXXXX")" || block "cannot create a build directory in $store"
  mkdir -- "$building/automation" || { rm -rf -- "$building"; block "cannot build in $building"; }
  while IFS= read -r entry; do
    ln -s -- "$gen/automation/$entry" "$building/automation/$entry" \
      || { rm -rf -- "$building"; block "cannot link $entry"; }
  done < <(expected)
  chmod 700 -- "$building" || { rm -rf -- "$building"; block "cannot chmod $building"; }
  mv -T -- "$building" "$store/$target" || { rm -rf -- "$building"; block "cannot place $store/$target"; }
fi
farm_matches "$store/$target" || block "$store/$target does not match $gen"

want="autophagy-import.d/$target"
if [[ "$(readlink -- "$link" 2>/dev/null || true)" != "$want" ]]; then
  ln -s -- "$want" "$link.pin.$$" || block "cannot create $link.pin.$$"
  mv -Tf -- "$link.pin.$$" "$link" || { rm -f -- "$link.pin.$$"; block "cannot switch $link"; }
fi
[[ "$(readlink -f -- "$link/automation/__init__.py")" == "$gen/automation/__init__.py" ]] \
  || block "$link does not resolve to $gen"
printf 'PIN-IMPORT-ROOT-OK %s\n' "$target"

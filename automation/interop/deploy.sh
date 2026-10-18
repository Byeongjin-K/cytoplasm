#!/usr/bin/env bash
# Deploy the interop gateway bundle to the agent and peer accounts (RCB todo 22).
#
# Declared in automation/interop/deploy-manifest.txt. Order is the safety argument:
#   (b) rule file: the gate code each account runs now must load the new file with the same
#       rule count (else rc 5, nothing replaced); a differing home copy is kept in this run's
#       ~/.hermes/interop/rollback/ (else rc 5, nothing replaced); then it lands on both accounts.
#   (c) roster gate: both rosters must validate, else INTEROP-DEPLOY-HELD and rc 0 — the
#       bundle lands on neither account (never on one account only).
#   (d) preflight: the gateway interpreter of each account imports the new shim the way the
#       gateway will (gateway_preflight.py) against the node release; any failure is rc 5.
#   (e) the current drop-in, plugin files and guard are copied to ~/.hermes/interop/rollback/.
#   (f) per account: guard + pin script -> drop-in -> daemon-reload -> plugin.yaml -> shim, so
#       no restart ever pairs the old unit environment with the new shim; (g) plugins enable
#       with stdin from /dev/null (its interactive question would swallow the script).
#   (g2) gate driver and the skill deployer's interop_runtime copy (interop/ and __init__.py
#       only; other files there belong to deploy-skill.sh).
#   (h) no restart unless --restart: then agent and peer restart and the gateway generation
#       must report ok on both within INTEROP_RESTART_WAIT seconds (default 120).
# Exit: 0 deployed or held · 2 usage · 3 no host · 4 provenance · 5 blocked
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
restart=0
case "${1:-}" in
  "") ;;
  --restart) restart=1 ;;
  *) echo "usage: automation/interop/deploy.sh [--restart]" >&2; exit 2 ;;
esac
[[ "${INTEROP_RESTART_WAIT:-120}" =~ ^[0-9]+$ && "${INTEROP_RESTART_POLL:-5}" =~ ^[0-9]+$ ]] \
  || { echo "usage: INTEROP_RESTART_WAIT and INTEROP_RESTART_POLL are whole seconds" >&2; exit 2; }

eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi

# shellcheck source=automation/deploy_provenance.sh
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/configs/external-effect-tools.yaml" \
  "$repo_root/configs/node.example.toml" \
  "$repo_root/automation/node_config.py" \
  "$repo_root/automation/__init__.py" \
  "$repo_root/automation/interop" || exit 4
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"
# shellcheck source=automation/deploy_tree.sh
source "$repo_root/automation/deploy_tree.sh"

accounts=("$NODE_AGENT_ACCOUNT" "$NODE_PEER_ACCOUNT")
gateway_python='"$HOME/.hermes/hermes-agent/venv/bin/python"'
# The node runtime root, resolved exactly like pin_import_root.sh and gateway_preflight.py.
resolve_root='root="${AUTOPHAGY_RUNTIME_ROOT:-}"; if [ -z "$root" ]; then root="${PIN_RELEASE_POINTER:-/srv/autophagy-agent-current}"; [ -e "$root" ] || root="${PIN_MIRROR_ROOT:-/srv/autophagy-agents}"; fi; '
rollback=".hermes/interop/rollback/$(date -u +%Y%m%dT%H%M%SZ)-$$"
rules="$repo_root/configs/external-effect-tools.yaml"
count_rules='import sys; from automation.interop.external_effect_gate import load_denylist; print(len(load_denylist(sys.argv[1])))'

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "cd \"\$HOME\" || exit 1; $script")"
}
block() { printf 'INTEROP-DEPLOY-BLOCK: %s\n' "$*" >&2; exit 5; }
interrupted() {
  block "$1 failed on $account; the previous bundle is kept in ~/$rollback on each account — re-run this deploy"
}

# (b) the gate code running now must read the new rule file before it replaces the old one.
want_rules="$(PYTHONPATH="$repo_root" python3 -B -c "$count_rules" "$rules")" \
  || block "the repository rule file does not parse"
for account in "${accounts[@]}"; do
  got="$(run_account "$account" 't="$(mktemp)" || exit 1; cat > "$t" || { rm -f "$t"; exit 1; }; if [ -e "$HOME/.hermes/autophagy-import" ]; then p="$HOME/.hermes/autophagy-import"; else p="$HOME/.hermes/interop_runtime"; fi; PYTHONPATH="$p" '"$gateway_python"' -B -c '"$(printf '%q' "$count_rules")"' "$t"; rc=$?; rm -f "$t"; exit "$rc"' < "$rules")" || got=""
  [[ "$got" == "$want_rules" ]] \
    || block "the gate code on $account does not read the new rule file (want $want_rules rules, got ${got:-none}); nothing replaced"
done
# (b2) the rule file moves even when the roster gate holds the bundle, so its previous bytes go to
#      this run's rollback first — on both accounts before either is replaced (v1.14.1 lost them).
want_sha="$(sha256sum -- "$rules")" || block "cannot hash the repository rule file"
want_sha="${want_sha%% *}"
for account in "${accounts[@]}"; do
  run_account "$account" 'umask 077; f="$HOME/.hermes/interop/external-effect-tools.yaml"; [ -e "$f" ] || exit 0; have="$(sha256sum -- "$f")" || exit 1; [ "${have%% *}" = '"$(printf '%q' "$want_sha")"' ] && exit 0; b="$HOME/"'"$(printf '%q' "$rollback")"'; mkdir -p -m 700 "$b/.hermes/interop" && cp -p "$f" "$b/.hermes/interop/external-effect-tools.yaml" && chmod 600 "$b/.hermes/interop/external-effect-tools.yaml"' </dev/null \
    || block "could not keep the current rule file of $account in ~/$rollback; nothing replaced"
done
for account in "${accounts[@]}"; do
  run_agent() { run_account "$account" "$1"; }
  push_file "$rules" .hermes/interop/external-effect-tools.yaml || block "the rule file did not land on $account"
done

# (c) roster gate on both accounts before any bundle file moves.
for account in "${accounts[@]}"; do
  if ! run_account "$account" "$resolve_root"'PYTHONPATH="$root" python3 -B -m automation.group_roster validate "$HOME/.hermes/roster.yaml"' </dev/null >/dev/null; then
    echo "INTEROP-DEPLOY-HELD: roster missing or invalid on $account"
    echo "  the rule file is converged; the gateway bundle stays off both accounts until both rosters validate"
    exit 0
  fi
done

# (d) import the new shim the way the gateway will, on both accounts, before writing anything.
preflight="$(<"$repo_root/automation/interop/gateway_preflight.py")"
for account in "${accounts[@]}"; do
  run_account "$account" "$gateway_python -B -c $(printf '%q' "$preflight") gateway-preflight" \
    < "$repo_root/automation/interop/plugin_shim/__init__.py" >/dev/null \
    || block "preflight import failed on $account"
done

# (e) keep the current bundle where Hermes does not look for plugins.
for account in "${accounts[@]}"; do
  run_account "$account" 'b="$HOME/'"$rollback"'"; mkdir -p -m 700 "$b" || exit 1; for f in .config/systemd/user/hermes-gateway.service.d/20-interop-guard.conf .hermes/plugins/interop-protocol/__init__.py .hermes/plugins/interop-protocol/plugin.yaml .hermes/interop/production_guard.sh; do if [ -e "$HOME/$f" ]; then mkdir -p "$b/${f%/*}" && cp -p "$HOME/$f" "$b/$f" || exit 1; fi; done' </dev/null \
    || block "could not back up the current bundle on $account; nothing replaced"
done

# (f)+(g) start scripts, then the unit environment, then the plugin.
for account in "${accounts[@]}"; do
  run_agent() { run_account "$account" "$1"; }
  push_file "$repo_root/automation/interop/production_guard.sh" .hermes/interop/production_guard.sh || interrupted "the guard"
  push_file "$repo_root/automation/interop/pin_import_root.sh" .hermes/interop/pin_import_root.sh || interrupted "the import-root pin"
  run_agent 'chmod 700 "$HOME/.hermes/interop/production_guard.sh" "$HOME/.hermes/interop/pin_import_root.sh"' </dev/null \
    || interrupted "making the start scripts executable"
  push_file "$repo_root/automation/interop/gateway-dropin.conf" .config/systemd/user/hermes-gateway.service.d/20-interop-guard.conf \
    || interrupted "the drop-in"
  run_agent 'XDG_RUNTIME_DIR="/run/user/$(id -u)" systemctl --user daemon-reload' </dev/null || interrupted "daemon-reload"
  push_file "$repo_root/automation/interop/hermes_plugin/plugin.yaml" .hermes/plugins/interop-protocol/plugin.yaml \
    || interrupted "the plugin manifest"
  push_file "$repo_root/automation/interop/plugin_shim/__init__.py" .hermes/plugins/interop-protocol/__init__.py \
    || interrupted "the plugin shim"
  run_agent 'PATH="$HOME/.local/bin:$PATH" hermes plugins enable interop-protocol < /dev/null' </dev/null \
    || interrupted "plugins enable"
done

# (g2) copies the gateway no longer imports, kept at release bytes for their other readers.
for account in "${accounts[@]}"; do
  run_agent() { run_account "$account" "$1"; }
  push_file "$repo_root/automation/interop/gate_driver.py" .hermes/interop/gate_driver.py || interrupted "the gate driver"
  run_agent 'chmod 700 "$HOME/.hermes/interop/gate_driver.py"' </dev/null || interrupted "making the gate driver executable"
  push_file "$repo_root/automation/__init__.py" .hermes/interop_runtime/automation/__init__.py \
    || interrupted "the runtime package marker"
  push_file "$repo_root/automation/node_config.py" .hermes/interop_runtime/automation/node_config.py \
    || interrupted "the node configuration loader"
  push_file "$repo_root/configs/node.example.toml" .hermes/interop_runtime/automation/node.example.toml \
    || interrupted "the node configuration seed"
  deploy_tree_swap "$repo_root/automation/interop" .hermes/interop_runtime/automation/interop \
    || interrupted "the interop runtime tree"
done

# (h)
if ((!restart)); then
  echo "INTEROP-DEPLOYED: gateway restart pending (agent+peer)"
  exit 0
fi
failed=()
for account in "${accounts[@]}"; do
  run_account "$account" 'XDG_RUNTIME_DIR="/run/user/$(id -u)" systemctl --user restart hermes-gateway.service' </dev/null \
    || failed+=("$account")
done
deadline=$((SECONDS + ${INTEROP_RESTART_WAIT:-120}))
while ((${#failed[@]} == 0)); do
  pending=()
  for account in "${accounts[@]}"; do
    run_account "$account" "$resolve_root"'PYTHONPATH="$root" python3 -B -m automation.gateway_generation --check --runtime-root "$root" --require interop-protocol' </dev/null \
      || pending+=("$account")
  done
  if ((${#pending[@]} == 0)); then
    echo "INTEROP-DEPLOYED: agent+peer gateways restarted on the release generation"
    exit 0
  fi
  ((SECONDS < deadline)) || { failed=("${pending[@]}"); break; }
  sleep "${INTEROP_RESTART_POLL:-5}"
done
block "the gateway generation is not ok on ${failed[*]} after the restart; the previous bundle is in ~/$rollback"

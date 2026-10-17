#!/usr/bin/env bash
# Read-only config drift detection, not a claim about the running gateway process.
set -euo pipefail
if [[ -z "${NODE_PEER_HOME:-}" || -z "${NODE_PEER_ACCOUNT:-}" || -z "${NODE_OPS_ACCOUNT:-}" ]]; then
  eval "$(python3 "$(dirname "${BASH_SOURCE[0]}")/node_config_sh.py" --print-env)"
fi
readonly PEER_GATEWAY_CONFIG="${HEALTHCHECK_PEER_GATEWAY_CONFIG:-$NODE_PEER_HOME/.hermes/config.yaml}"
readonly PEER_INTEROP_CONFIG="${HEALTHCHECK_PEER_INTEROP_CONFIG:-$NODE_PEER_HOME/.hermes/interop/config.json}"

peer_ignored_channels_guidance() {
  printf '%s\n' \
    'PEER-IGNORED-CHANNELS-RECOVERY: OWNER restore top-level discord.ignored_channels from personal_approvals_channel_id, deploy_approvals_channel_id and owner_notice_channel_id in the peer interop config; then follow operations.md gateway-pair restart rules. Never auto-edit config or restart.' \
    'PEER-IGNORED-CHANNELS-RECOVERY: if unreadable, OWNER review/install these exact read-only sudoers entries with visudo (no shell or Python sudo grant):'
  printf '%s ALL=(%s) NOPASSWD: /usr/bin/cat -- %s\n' "$NODE_OPS_ACCOUNT" "$NODE_PEER_ACCOUNT" "$PEER_GATEWAY_CONFIG" "$NODE_OPS_ACCOUNT" "$NODE_PEER_ACCOUNT" "$PEER_INTEROP_CONFIG"
  printf '%s\n' 'PEER-IGNORED-CHANNELS-RECOVERY: parser unavailable: OWNER provide python3 with PyYAML (Debian/Ubuntu: apt-get install python3-yaml).'
}

peer_gateway_read() {
  # Only these two fixed paths are passed by the probe; data never reaches argv/logs.
  if [[ -r "$1" ]]; then
    /usr/bin/cat -- "$1" 2>/dev/null
  else
    sudo -n -u "$NODE_PEER_ACCOUNT" /usr/bin/cat -- "$1" 2>/dev/null
  fi
}

probe_peer_ignored_channels() {
  local config interop
  if ! config="$(peer_gateway_read "$PEER_GATEWAY_CONFIG")" \
    || ! interop="$(peer_gateway_read "$PEER_INTEROP_CONFIG")"; then
    printf '%s\n' '[healthcheck] PEER-IGNORED-CHANNELS-UNREADABLE'
    peer_ignored_channels_guidance
    return 1
  fi
  # A full YAML parser rejects malformed surrounding config as well as this field.
  # PyYAML is already present in the node's ops python3 and peer Hermes venv.
  # Keep config (which can contain secrets) on inherited fds, never argv or env.
  if python3 - 4<<< "$config" 5<<< "$interop" <<'PY'
from __future__ import annotations

import json
import os
from typing import NewType

try:
    import yaml
except ImportError:
    print("[healthcheck] PEER-IGNORED-CHANNELS-PARSER-UNAVAILABLE")
    raise SystemExit(1) from None

ChannelId = NewType("ChannelId", str)


class ProbeInputError(Exception):
    """An input cannot establish the required exclusion; contents stay private."""


def field(node: yaml.Node, name: str) -> yaml.Node:
    """Parse a unique mapping field, rejecting duplicate or missing keys."""
    if not isinstance(node, yaml.MappingNode):
        raise ProbeInputError
    matches = [value for key, value in node.value
               if isinstance(key, yaml.ScalarNode) and key.value == name]
    if len(matches) != 1:
        raise ProbeInputError
    return matches[0]


try:
    with os.fdopen(5, encoding="utf-8") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict):
        raise ProbeInputError
    required: set[ChannelId] = set()
    for key in ("personal_approvals_channel_id", "deploy_approvals_channel_id"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.isascii() or not value.isdigit():
            raise ProbeInputError
        required.add(ChannelId(value))
    if not required:
        raise ProbeInputError
    notice = raw.get("owner_notice_channel_id")
    if notice is not None:
        if not isinstance(notice, str) or not notice.isascii() or not notice.isdigit():
            raise ProbeInputError
        required.add(ChannelId(notice))
    with os.fdopen(4, encoding="utf-8") as stream:
        root = yaml.compose(stream, Loader=yaml.SafeLoader)
    ignored = field(field(root, "discord"), "ignored_channels")
    # Parse untrusted YAML node kinds here; internal membership uses ChannelId only.
    if isinstance(ignored, yaml.SequenceNode):
        entries = ignored.value
        if not all(isinstance(entry, yaml.ScalarNode) and entry.tag in (
            "tag:yaml.org,2002:str", "tag:yaml.org,2002:int",
        ) for entry in entries):
            raise ProbeInputError
        # Hermes does not split CSV inside a list item.
        ids = {ChannelId(entry.value.strip()) for entry in entries}
    elif isinstance(ignored, yaml.ScalarNode) and ignored.tag == "tag:yaml.org,2002:str":
        ids = {ChannelId(part.strip()) for part in ignored.value.split(",")}
    else:
        raise ProbeInputError
    if not required <= ids:
        print("[healthcheck] PEER-IGNORED-CHANNELS-MISSING")
        raise SystemExit(1)
except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError, ProbeInputError):
    print("[healthcheck] PEER-IGNORED-CHANNELS-INVALID")
    raise SystemExit(1) from None
print("[healthcheck] PEER-IGNORED-CHANNELS-PASS")
PY
  then
    return 0
  fi
  peer_ignored_channels_guidance
  return 1
}

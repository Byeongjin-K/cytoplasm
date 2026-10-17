#!/usr/bin/env bash
# Read-only config drift detection, not a claim about the running gateway process.
set -euo pipefail
if [[ -z "${NODE_PEER_HOME:-}" || -z "${NODE_PEER_ACCOUNT:-}" || -z "${NODE_OPS_ACCOUNT:-}" ]]; then
  eval "$(python3 "$(dirname "${BASH_SOURCE[0]}")/node_config_sh.py" --print-env)"
fi
readonly PEER_GATEWAY_CONFIG="${HEALTHCHECK_PEER_GATEWAY_CONFIG:-$NODE_PEER_HOME/.hermes/config.yaml}"
readonly PEER_CHANNEL_DIRECTORY="${HEALTHCHECK_PEER_CHANNEL_DIRECTORY:-$NODE_PEER_HOME/.hermes/channel_directory.json}"

peer_ignored_channels_guidance() {
  printf '%s\n' \
    'PEER-IGNORED-CHANNELS-RECOVERY: OWNER restore top-level discord.ignored_channels from the unique approvals entry (plus the notifications entry when the peer directory lists one) in the peer channel directory; then follow operations.md gateway-pair restart rules. Never auto-edit config or restart.' \
    'PEER-IGNORED-CHANNELS-RECOVERY: if unreadable, OWNER review/install these exact read-only sudoers entries with visudo (no shell or Python sudo grant):'
  printf '%s ALL=(%s) NOPASSWD: /usr/bin/cat -- %s\n' "$NODE_OPS_ACCOUNT" "$NODE_PEER_ACCOUNT" "$PEER_GATEWAY_CONFIG" "$NODE_OPS_ACCOUNT" "$NODE_PEER_ACCOUNT" "$PEER_CHANNEL_DIRECTORY"
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
  local config directory
  if ! config="$(peer_gateway_read "$PEER_GATEWAY_CONFIG")" \
    || ! directory="$(peer_gateway_read "$PEER_CHANNEL_DIRECTORY")"; then
    printf '%s\n' '[healthcheck] PEER-IGNORED-CHANNELS-UNREADABLE'
    peer_ignored_channels_guidance
    return 1
  fi
  # A full YAML parser rejects malformed surrounding config as well as this field.
  # PyYAML is already present in the node's ops python3 and peer Hermes venv.
  # Keep config (which can contain secrets) on inherited fds, never argv or env.
  # The memo holds only channel ids this probe already matched (ops-owned, 0600), so
  # renaming #approvals/#notifications in Discord keeps the same ids required.
  local memo="${HEALTHCHECK_PEER_IGNORED_MEMO:-${LOG_DIR:+$LOG_DIR/peer-ignored-ids.json}}"
  if PEER_IGNORED_MEMO="$memo" python3 - 4<<< "$config" 5<<< "$directory" <<'PY'
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
    if not isinstance(raw, dict) or not isinstance(raw.get("platforms"), dict):
        raise ProbeInputError
    channels = raw["platforms"].get("discord")
    if not isinstance(channels, list) or not all(isinstance(row, dict) for row in channels):
        raise ProbeInputError

    def unique_channel(name: str, *, required: bool) -> ChannelId | None:
        """One channel by name; absence is allowed only for optional surfaces."""
        matches = [row.get("id") for row in channels if row.get("name") == name]
        if not matches and not required:
            return None
        if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0].isascii() or not matches[0].isdigit():
            raise ProbeInputError
        return ChannelId(matches[0])

    # A channel matched once by its conventional name stays required by id while the
    # peer directory still lists that id, so a Discord rename does not drop it
    # (owner 2026-10-18 "Approval도 코드 변경해서 같은 느낌으로 맞춰줘").
    present = {row.get("id") for row in channels if isinstance(row.get("id"), str)}
    memo_path = os.environ.get("PEER_IGNORED_MEMO", "")
    remembered: dict[str, list[str]] = {}
    if memo_path:
        try:
            with open(memo_path, encoding="utf-8") as stream:
                loaded = json.load(stream)
            if isinstance(loaded, dict):
                remembered = {key: [i for i in value if isinstance(i, str) and i.isascii() and i.isdigit()]
                              for key, value in loaded.items() if isinstance(value, list)}
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError, json.JSONDecodeError):
            print("[healthcheck] PEER-IGNORED-CHANNELS-MEMO-UNREADABLE")

    def channel_ids(name: str, *, required: bool) -> set[ChannelId]:
        """The name's current id plus earlier matches the directory still lists."""
        found = unique_channel(name, required=False)
        ids = {ChannelId(i) for i in remembered.get(name, []) if i in present}
        if found is not None:
            ids.add(found)
        if required and not ids:
            raise ProbeInputError
        return ids

    # #approvals must exist; #notifications is required only when the peer can see it —
    # a peer that receives owner notices opens a thread on every chunk (2026-09-21).
    approvals = channel_ids("approvals", required=True)
    notifications = channel_ids("notifications", required=False)
    required = approvals | notifications
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
if memo_path:
    memo = {"approvals": sorted(approvals), "notifications": sorted(notifications)}
    try:
        temporary = f"{memo_path}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(memo, stream)
        os.replace(temporary, memo_path)
    except OSError:
        # The verdict stands; the next run simply matches by name again.
        print("[healthcheck] PEER-IGNORED-CHANNELS-MEMO-WRITE-FAIL")
print("[healthcheck] PEER-IGNORED-CHANNELS-PASS")
PY
  then
    return 0
  fi
  peer_ignored_channels_guidance
  return 1
}

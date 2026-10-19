"""Whether a node has actually been configured, kept apart from parsing it.

node_config.py owns reading and validating the file; this owns the question the
2026-08-16 outage turned out to hinge on — is this the shipped seed? Splitting it
out keeps that module under the 250-line ceiling rather than one line below it.
"""

from __future__ import annotations

import sys
from typing import Final

from automation.node_config import (
    SYSTEM_NODE_CONFIG_PATH,
    NodeConfig,
    default_node_config,
    load_node_config,
)

# Identity fields whose seed values are, by construction, never right on a real node.
# A node that still carries them is not "configured differently" — it is unconfigured,
# and the failures that follow (ssh to example-primary-node, ls-remote to
# example.invalid) name the placeholder rather than the omission.
_IDENTITY_FIELDS: Final = ("primary_node_name", "rag_node_name", "deploy_ssh_host", "origin_url")


def unconfigured_reason(config: NodeConfig) -> str | None:
    """Return why this node counts as unconfigured, or None when it is set up.

    Wrong values announce themselves; missing ones did not. On 2026-08-16 the seed
    fallback sent an approved deploy ssh-ing to `example-primary-node` (exit 4) and
    made the reconciler skip every tick with rc 0 — visible only to someone reading
    journals for an unrelated reason.
    """
    seed = default_node_config()
    stale = tuple(name for name in _IDENTITY_FIELDS if getattr(config, name) == getattr(seed, name))
    if not stale:
        return None
    return (
        f"node is not configured: identity is still the shipped seed ({', '.join(stale)}) — "
        f"write the real values to {SYSTEM_NODE_CONFIG_PATH} or ~/.hermes/node.toml"
    )


def main() -> int:
    """Refuse deploy entry points before placeholder hostnames can reach DNS."""
    from automation.node_config_sh import apply_system_override

    apply_system_override()  # the same system-file lookup as the shell bridge beside it
    reason = unconfigured_reason(load_node_config())
    if reason is None:
        return 0
    print(f"NODE-CONFIG-BLOCK: {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

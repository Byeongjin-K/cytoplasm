"""Retired Hermes interop plugin — inert tombstone for v2.6.6-era nodes.

A node that last converged on v2.6.6 still has the retired Hermes drop-in, and its
home copy of pin_import_root.sh refuses to start the gateway (exit 78) unless this path
exists in the new release tree. The deploy-reconcile tick that moves such a node is the
old generation's code, so it restarts the gateway and rolls the release back on that
failure (issue #150). This file exists only so that check passes; the retired_jobs
"interop-plugin" cleanup removes the drop-in once the transition lands.

That old home shim imports this module and calls `register`. The Hermes gateway is
retired, so the hooks registered here make it inert and fail closed: every inbound
event is skipped and every tool call is blocked. There is no evaluator and no allow path.
"""
from __future__ import annotations

import logging
from typing import Final

LOGGER: Final = logging.getLogger("autophagy.interop")
SKIP: Final = {"action": "skip", "reason": "interop_retired"}
BLOCK: Final = {"action": "block", "message": "BLOCKED: the Hermes gateway is retired on this node"}


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    LOGGER.warning("interop plugin retired: gateway inert, every tool call blocked")


def pre_gateway_dispatch(event, gateway, session_store, **kwargs) -> dict[str, str]:
    del event, gateway, session_store, kwargs
    return dict(SKIP)


def pre_tool_call(tool_name, args, **kwargs) -> dict[str, str]:
    del args, kwargs
    LOGGER.warning("retired interop plugin blocked tool=%s", tool_name)
    return dict(BLOCK)

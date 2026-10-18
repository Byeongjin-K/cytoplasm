"""Config observation algorithms and their bounded, closed value grammar."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias

from automation import hermes_memory_config, hermes_soul_recall, yoodori_channel
from automation.deploy_declarations import Declaration
from automation.memory_provider import agent_config as agent_profile
from automation.memory_provider import config as memory_profile

if TYPE_CHECKING:
    from automation.deploy_all_observe import Runner

CONFIG_MODULES: Final = {
    hermes_memory_config.ALGORITHM: hermes_memory_config,
    hermes_soul_recall.ALGORITHM: hermes_soul_recall,
    memory_profile.ALGORITHM: memory_profile,
    agent_profile.ALGORITHM: agent_profile,
    yoodori_channel.ALGORITHM: yoodori_channel,
}
_PAIRS: Final = r"([a-z_]+=[a-z0-9_-]+(?:,[a-z_]+=[a-z0-9_-]+)*)"
ConfigFacts: TypeAlias = tuple[str, str, str, tuple[tuple[str, str], ...]]


def observe_config(
    decl: Declaration, root: Path, call: Runner, *, dori_mode: str | None = None,
    chat_channel: str = "off",
) -> ConfigFacts:
    """`dori_mode` is the release host's switch as handed to the node probe; None reads it here (host only)."""
    module = CONFIG_MODULES.get(decl.attr("algorithm"))
    if module is None:
        return "?", "?", "unknown", (("unsupported-algorithm", "-"),)
    try:
        if module is yoodori_channel:
            wanted = module.wanted_identity(yoodori_channel=chat_channel)
        elif module is agent_profile:
            wanted = module.wanted_identity(dori_agent_chat=None if dori_mode is None else dori_mode == "on",
                                            agent_chat_channel=chat_channel)
        else:
            wanted = module.wanted_identity()
    except (OSError, UnicodeError, ValueError):
        return "?", "?", "unknown", (("invalid-release-switch", "-"),)
    want_sha = hashlib.sha256(wanted.encode()).hexdigest()[:16]
    extra = (
        ("--dori-agent-chat", "on" if dori_mode == "on" or (
            dori_mode is None and "agent_chat_ignored=yes" in wanted) else "off")
        if module is agent_profile else ()
    )
    if module is yoodori_channel:
        extra = ("--yoodori-channel", chat_channel, "--role", decl.account)
    elif module is agent_profile and chat_channel != "off":
        extra = (*extra, "--yoodori-channel", chat_channel)
    rc, out = call(decl.account, ("config", str(root), module.__name__, *extra))
    match = re.fullmatch(rf"{re.escape(module.MARKER)} have={_PAIRS}", out.strip()) if rc == 0 else None
    if match is None:
        return want_sha, "?", "unknown", (("unreadable-config", "-"),)
    have = dict(item.split("=", 1) for item in match[1].split(","))
    want = dict(item.split("=", 1) for item in wanted.split(","))
    details = tuple((f"different-{key}", "-") for key, value in want.items() if have.get(key) != value)
    have_sha = hashlib.sha256(match[1].encode()).hexdigest()[:16]
    return want_sha, have_sha, "stale" if details else "ok", details

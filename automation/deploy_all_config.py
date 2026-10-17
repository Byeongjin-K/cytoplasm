"""Config observation algorithms and their bounded, closed value grammar."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias

from automation import hermes_memory_config, hermes_soul_recall
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
}
_PAIRS: Final = r"([a-z_]+=[a-z0-9_-]+(?:,[a-z_]+=[a-z0-9_-]+)*)"
ConfigFacts: TypeAlias = tuple[str, str, str, tuple[tuple[str, str], ...]]


def observe_config(decl: Declaration, root: Path, call: Runner) -> ConfigFacts:
    module = CONFIG_MODULES.get(decl.attr("algorithm"))
    if module is None:
        return "?", "?", "unknown", (("unsupported-algorithm", "-"),)
    wanted = module.wanted_identity()
    want_sha = hashlib.sha256(wanted.encode()).hexdigest()[:16]
    rc, out = call(decl.account, ("config", str(root), module.__name__))
    match = re.fullmatch(rf"{re.escape(module.MARKER)} have={_PAIRS}", out.strip()) if rc == 0 else None
    if match is None:
        return want_sha, "?", "unknown", (("unreadable-config", "-"),)
    have = dict(item.split("=", 1) for item in match[1].split(","))
    want = dict(item.split("=", 1) for item in wanted.split(","))
    details = tuple((f"different-{key}", "-") for key, value in want.items() if have.get(key) != value)
    have_sha = hashlib.sha256(match[1].encode()).hexdigest()[:16]
    return want_sha, have_sha, "stale" if details else "ok", details

"""Legacy import-farm helpers retained for diagnostics, without Hermes hook loading.

The interop deployer no longer installs this helper or any gateway drop-in.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

REQUIRED: Final = (
    "automation/__init__.py",
    "automation/gateway_generation.py",
    "automation/interop/approval_types.py",
)
EXCLUDED: Final = frozenset({"hermes_compat", "__pycache__"})


def resolve_root(env: Mapping[str, str]) -> Path:
    """Resolve an explicit root, then the release pointer, then the mirror."""
    override = env.get("AUTOPHAGY_RUNTIME_ROOT")
    if override:
        return Path(override)
    pointer = Path(env.get("PIN_RELEASE_POINTER") or "/srv/autophagy-agent-current")
    if os.path.lexists(pointer) and pointer.exists():
        return pointer
    return Path(env.get("PIN_MIRROR_ROOT") or "/srv/autophagy-agents")


def missing_closure(generation: Path) -> list[str]:
    return [relative for relative in REQUIRED if not (generation / relative).is_file()]


def build_farm(generation: Path, farm: Path) -> None:
    package = farm / "automation"
    package.mkdir(parents=True)
    for entry in sorted((generation / "automation").iterdir()):
        if entry.name in EXCLUDED or entry.name.startswith("."):
            continue
        (package / entry.name).symlink_to(generation / "automation" / entry.name)

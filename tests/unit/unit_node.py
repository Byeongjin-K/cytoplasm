"""Node-state isolation for child processes that a test starts with a hand-built env.

``conftest.py`` points every node default the suite reaches (live skill mount, system
node config, node mirror logs, shared /tmp locks, ...) at absent paths under one
temporary root, through ``os.environ``. A child started with ``env={...}`` does not
inherit that, so on the primary node it reads the real install again. Merge
``env()`` first and let the test's own keys win::

    subprocess.run(argv, env={**unit_node.env(), "HOME": str(tmp_path), ...})
"""
from __future__ import annotations

import os


def env() -> dict[str, str]:
    """The isolation variables conftest exported, by name."""
    keys = os.environ["AUTOPHAGY_UNIT_NODE_ENV_KEYS"].split(",")
    return {key: os.environ[key] for key in keys}

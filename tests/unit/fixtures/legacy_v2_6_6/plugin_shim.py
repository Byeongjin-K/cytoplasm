"""Entry point installed as ``~/.hermes/plugins/interop-protocol/__init__.py``.

The plugin body comes from the release generation the gateway pinned on its
import path, not from this home copy. The gate is always registered; recording
the generation is optional so an older import path still gets the gate and the
missing record surfaces as stale.
"""

from __future__ import annotations

from types import ModuleType

from automation.interop import hermes_plugin

_generation: ModuleType | None
try:
    from automation import gateway_generation as _generation
except ImportError:
    _generation = None

PLUGIN_NAME = "interop-protocol"


def register(ctx) -> None:
    hermes_plugin.register(ctx)
    if _generation is not None:
        _generation.record(PLUGIN_NAME, __file__)

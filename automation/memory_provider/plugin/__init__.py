"""Hermes MemoryProvider discovered at $HERMES_HOME/plugins/autophagy_recall/.

Only prefetch is active: native memory, system prompt, and sync hooks are untouched.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Final, Never

from agent.memory_provider import MemoryProvider

_TIMEOUT_SECONDS: Final = 2.0
_CHAR_LIMIT: Final = 3000
_QUERY_LIMIT: Final = 2000
_LOGGER: Final = logging.getLogger(__name__)


class AutophagyRecallProvider(MemoryProvider):
    """Stateless turn recall; pin the released worker generation for this instance."""

    def __init__(self) -> None:
        self._root = Path(os.environ.get(
            "AUTOPHAGY_RUNTIME_ROOT", "/srv/autophagy-agent-current",
        )).expanduser().resolve()

    @property
    def name(self) -> str:
        return "autophagy_recall"

    def is_available(self) -> bool:
        """No network check during discovery."""
        return (self._root / "automation/memory_provider/worker.py").is_file()

    def initialize(self, session_id: str, **kwargs: str) -> None:
        """No store, per-session cache, or conversation writes."""

    def get_tool_schemas(self) -> list[Never]:
        return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Bound the whole lookup, including process startup and MCP connection."""
        if not query.strip():
            return ""
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(self._root)
        try:
            result = subprocess.run(
                [sys.executable, "-B", "-m", "automation.memory_provider.worker"],
                input=query[:_QUERY_LIMIT],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                cwd=self._root,
                env=environment,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _LOGGER.warning("MEMORY-RECALL-SKIP reason=timeout")
            return ""
        except (OSError, UnicodeError):
            _LOGGER.warning("MEMORY-RECALL-SKIP reason=process")
            return ""
        if result.returncode != 0:
            _LOGGER.warning("MEMORY-RECALL-SKIP reason=lookup")
            return ""
        if len(result.stdout) > _CHAR_LIMIT:
            _LOGGER.warning("MEMORY-RECALL-SKIP reason=oversize")
            return ""
        return result.stdout

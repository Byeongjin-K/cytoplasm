#!/usr/bin/env python3
"""Shared auto-write daily summary; no model and no Discord receive polling."""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    root = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    runtime = Path(root).expanduser() if root else Path("/srv/autophagy-agent-current")
    if not runtime.exists():
        runtime = Path("/srv/autophagy-agents")
    sys.path.insert(0, str(runtime))
    from automation import auto_write
    from automation.plaud_sync.watch_runtime import load_env_secrets

    load_env_secrets()
    try:
        auto_write.summarize(datetime.now(UTC))
    except (OSError, ValueError) as error:
        print(f"AUTO-WRITE-SUMMARY-FAIL reason={type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

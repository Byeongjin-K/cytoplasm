"""Node transport; the release-installed marker selects this route, never the host flag."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from automation.omo_relay.config import RelayConfigError, load as load_relay
from automation.omo_relay.availability import record_result
from automation.omo_relay_policy import (
    CARD_LINE,  # noqa: F401 - public relay metadata re-export
    MODE,  # noqa: F401 - public relay metadata re-export
    SURFACE,  # noqa: F401 - public relay metadata re-export
    enabled,  # noqa: F401 - public route selector re-export
)

if TYPE_CHECKING:
    from automation.codex_llm import Served

def argv(home: Path) -> list[str]:
    config = load_relay(home / ".config/autophagy/omo-relay/node.toml")
    known = home / ".config/autophagy/omo-relay/known_hosts"
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known}", "-o", "GlobalKnownHostsFile=/dev/null",
            "-i", str(config.omo_relay_key), config.omo_relay_target]


def complete(home: str, prompt: str, timeout: float) -> Served:
    """Retry transport/overload once, preserving the public error and Served types."""
    from automation.codex_llm import CodexError, CodexUnavailableError, Served

    try:
        command = argv(Path(home))
    except (OSError, RelayConfigError):
        raise CodexUnavailableError("omo relay node configuration unavailable") from None
    for attempt in range(2):
        try:
            result = subprocess.run(
                command, input=prompt, text=True, capture_output=True, check=False,
                timeout=timeout, cwd=tempfile.gettempdir(),
                env={"HOME": home, "PATH": f"{home}/.local/bin:/usr/bin:/bin"},
            )
        except (OSError, subprocess.TimeoutExpired):
            retry = True
        else:
            retry = result.returncode in (75, 124, 255) or any(
                token in result.stderr.lower() for token in ("429", "too many requests", "rate limit")
            )
            if not retry:
                if result.returncode:
                    record_result(Path(home), False)
                    raise CodexUnavailableError(f"omo relay failed (rc={result.returncode})")
                try:
                    raw = json.loads(result.stdout)
                except ValueError:
                    record_result(Path(home), False)
                    raise CodexError("omo relay returned invalid JSON") from None
                if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
                    record_result(Path(home), False)
                    raise CodexError("omo relay returned invalid answer")
                text = raw["text"].strip()
                if not text:
                    record_result(Path(home), False)
                    raise CodexError("omo relay returned an empty completion")
                record_result(Path(home), True)
                return Served(text, str(raw.get("provider") or "unknown"),
                              str(raw.get("model") or "unknown"))
        if attempt == 0:
            time.sleep(30)
    record_result(Path(home), False)
    raise CodexUnavailableError("omo relay unavailable after one retry")

"""Node transport; the release-installed marker selects this route, never the host flag."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from automation.node_config import NodeConfigError, load_node_config
from automation.omo_relay.availability import record_result

if TYPE_CHECKING:
    from automation.codex_llm import Served

MODE = ".hermes/omo-relay/mode"
SURFACE = "omo-relay"
CARD_LINE = "배치 모델 호출: 노드 → 이 PC omo(도구 없음), 파일 삭제 후 재배포로 Hermes 호출 복귀"


def enabled(home: Path) -> bool:
    try:
        value = (home / MODE).read_bytes()
    except FileNotFoundError:
        return False
    if value != b"on":
        raise ValueError("invalid installed omo-relay mode")
    return True


def argv(home: Path) -> list[str]:
    config = load_node_config(home / ".hermes/omo-relay/node.toml")
    return ["ssh", "-T", "-o", "BatchMode=yes", "-i",
            str(config.omo_relay_key), config.omo_relay_target]


def complete(home: str, prompt: str, timeout: float) -> Served:
    """Retry transport/overload once, preserving the public error and Served types."""
    from automation.codex_llm import CodexError, CodexUnavailableError, Served

    try:
        command = argv(Path(home))
    except (OSError, NodeConfigError):
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

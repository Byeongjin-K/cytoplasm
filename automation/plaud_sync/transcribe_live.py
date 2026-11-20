"""Live bindings for the local transcription step — MCP, locks, and the cron seam.

The long-running step runs after the tick releases ``watch.lock``; ``LiveEffects``
re-takes it only for the state compare-and-save. The concrete MCP, filesystem, and
subprocess effects live in ``transcribe_live_effects`` so this facade remains below
F2's 250-LOC ceiling without changing public imports or test monkeypatch seams.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Final

from automation import pipeline_lock
from automation.typing_compat import override

from .duration import DEFAULT_MIN_DURATION_MS
from .mcp_client import PlaudMcpClient as PlaudMcpClient
from .model import PlaudSyncState
from .store import load_state as load_state
from .transcribe import DEFAULT_MAX_ATTEMPTS, candidates, run_step
from .transcribe_journal import StepSummary as StepSummary, summarize
from .transcribe_live_effects import (
    CLI_ENV as CLI_ENV,
    DEFAULT_CLI_TIMEOUT as DEFAULT_CLI_TIMEOUT,
    SCRIPTS_ENV as SCRIPTS_ENV,
    LiveEffects as _LiveEffects,
    cli_path as cli_path,
)
from .transcribe_model import Outcome
from .transcribe_policy import DEFAULT_GIVE_UP, utc_now

KILL_SWITCH_ENV: Final = "PLAUD_SYNC_TRANSCRIBE"
BUSY_LINE: Final = "plaud-sync: transcribe busy (pipeline lock held)"


class LiveEffects(_LiveEffects):
    """Compatibility facade preserving the original injectable MCP and state seams."""

    @override
    def _mcp_client(self) -> PlaudMcpClient:
        return PlaudMcpClient()

    @override
    def _load_state(self) -> PlaudSyncState:
        return load_state(self.state_path)


def enabled(env: Mapping[str, str]) -> bool:
    return env.get(KILL_SWITCH_ENV, "1").strip() != "0"


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def run_transcribe_step(
    *, state_dir: Path, lock_path: Path, env: Mapping[str, str],
    now: Callable[[], datetime] = utc_now, deadline: float | None = None,
) -> StepSummary | None:
    """One transcription slot; deadline is the monotonic instant the job running it is killed."""
    if not enabled(env):
        return None
    state_path = state_dir / "state.json"
    limit = _env_int(env, "PLAUD_SYNC_TRANSCRIBE_PER_TICK", 1)
    outcomes: tuple[tuple[str, Outcome], ...] = ()
    with pipeline_lock.hold(env) as acquired:
        state = load_state(state_path)
        if not candidates(state, limit=1):
            return None
        outcomes = run_step(
            state,
            effects=LiveEffects(state_dir=state_dir, lock_path=lock_path, env=env, deadline=deadline),
            limit=limit if acquired else 0,
            max_attempts=_env_int(env, "PLAUD_SYNC_TRANSCRIBE_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
            give_up=_env_int(env, "PLAUD_SYNC_TRANSCRIBE_GIVE_UP", DEFAULT_GIVE_UP),
            min_duration_ms=_env_int(env, "PLAUD_SYNC_MIN_DURATION_MS", DEFAULT_MIN_DURATION_MS),
            now=now,
        )
    if not acquired and not outcomes:
        return StepSummary(BUSY_LINE, 0)
    return summarize(outcomes, load_state(state_path))

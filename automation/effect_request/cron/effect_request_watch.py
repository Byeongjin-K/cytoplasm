"""No-agent, reaction-only driver for owner-approved generic effect requests (Drive save, command)."""

from __future__ import annotations

import fcntl
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path


# Runtime root order (DG-4): AUTOPHAGY_REPO_ROOT override, else the release
# `current` symlink, else the resident mirror. Inlined by value because this
# wrapper sets sys.path BEFORE it can import automation.runtime_root.
def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if current.exists() else Path("/srv/autophagy-agents")


_REPO_ROOT = _runtime_root()
if (_REPO_ROOT / "automation").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation.drive_outputs import publish  # noqa: E402
from automation.effect_request.gate import discord_runtime, owner_id_from_config  # noqa: E402
from automation.effect_request.model import EffectRequestStore  # noqa: E402
from automation.effect_request.watch import run_tick  # noqa: E402

ENV_SECRETS = Path.home() / ".env.secrets"
_LONG_DIGITS = re.compile(r"\d{5,}")
_SECRET_VALUE = re.compile(r"(?i)(token|secret|password|key)=[^\s]+")


def _load_env_secrets(path: Path = ENV_SECRETS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        key, separator, value = raw_line.strip().partition("=")
        if separator and key and not key.startswith("#") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main() -> int:
    """Quiet unless an effect ran, was cancelled or failed this tick."""
    from automation.notice_bot import resolve_token

    try:
        _load_env_secrets()
        store = EffectRequestStore()
        if not any(record.status in {"posted", "approved", "running"} for record in store.all()):
            return 0
        store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (store.root / "watch.lock").open("a", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return 0
            token = resolve_token()
            runtime = discord_runtime(token, owner_id_from_config(), store)
            result = run_tick(store, runtime, token=token, now=datetime.now(UTC), publisher=publish)
        if result.done or result.cancelled or result.failed:
            print(
                f"effect-request: done={len(result.done)} "
                f"cancelled={len(result.cancelled)} failed={len(result.failed)}"
            )
    except Exception as error:  # noqa: BLE001 - final cron alert boundary
        message = _LONG_DIGITS.sub("[MASKED-NUM]", _SECRET_VALUE.sub(r"\1=[MASKED]", str(error)))
        print(f"effect-request-watch error: {message[:300]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

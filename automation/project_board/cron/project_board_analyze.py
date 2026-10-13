"""No-agent analysis tick: new institution mail → project proposal cards (opt-in)."""

from __future__ import annotations

import fcntl
import os
import re
import sys
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

from automation.project_board import sources  # noqa: E402
from automation.project_board.cli import today_kst, post_card  # noqa: E402
from automation.project_board.config import load_config  # noqa: E402
from automation.project_board.llm import provider_from_config  # noqa: E402
from automation.project_board.service import Board, analyze_new_mail  # noqa: E402

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
    """Silent while ``analyze.enabled`` is false; otherwise one line per tick that proposed."""
    try:
        _load_env_secrets()
        config = load_config()
        if not config.analyze_enabled:
            return 0
        board = Board.default()
        board.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (board.root / "analyze.lock").open("a", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return 0
            tick = analyze_new_mail(
                board, sources.mail_runtime(), provider_from_config(config.llm), config,
                today=today_kst(), post=lambda record: post_card(board, record),
            )
        if tick.proposed or tick.deferred:
            print(
                f"project-board-analyze: proposed={len(tick.proposed)} unrelated={tick.unrelated} "
                f"skipped={tick.skipped}" + (f" deferred={tick.deferred[:160]}" if tick.deferred else "")
            )
    except Exception as error:  # noqa: BLE001 - final cron alert boundary
        message = _LONG_DIGITS.sub("[MASKED-NUM]", _SECRET_VALUE.sub(r"\1=[MASKED]", str(error)))
        print(f"project-board-analyze error: {message[:300]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Run the governed mail watcher and expose failures to the timer runner.

Success is silent. Failures retain a masked diagnostic and nonzero exit so
doctor can read the timer result; no watch-failure incident is recorded."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# The streak helper is deployed flat beside this wrapper (~/.hermes/scripts/), and sits
# beside it in the repo too, so one path insert covers both. A partial deploy must not
# take the watcher down, hence the fallback (poll_reminders.py precedent).
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import watch_failure_streak
except ImportError:  # pragma: no cover — only reachable on a half-deployed node
    watch_failure_streak = None

WATCH_NAME = "mail-triage-watch"
#: `*/10` — 50 minutes of consecutive failure before the owner is told, once.
FAILURE_NOTICE_THRESHOLD = 5
CLI = Path("/srv/autophagy-skills/live/mail/scripts/triage_cli.py")
_ENV_SECRETS = Path.home() / ".env.secrets"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


def _runtime_root() -> Path:
    """Where the ``automation`` package lives (DG-4 order).

    The child resolves ``automation.interop`` to reach the shared approval façade.
    From a mounted release its own depth guess lands on
    ``/srv/autophagy-skills/releases``, which carries no automation package, and the
    adapter then refuses the request (``GATE-REFUSED … 승인 게시 거부``) — an owner ✅
    that resolves to nothing. Per the no-agent cron rule (b-2) the parent states the
    runtime root explicitly instead of letting the child guess.
    """
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if (current / "automation").is_dir() else Path("/srv/autophagy-agents")


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron hands the wrapper no secrets, so the parent loads them itself.

    This watcher is the only thing that turns an owner ✅ into a sent mail, and its
    child needs the Discord and mail credentials. Measured 2026-08-18 on `budget-watch`:
    without this step the configuration exists on disk and still never reaches the
    child (규약 (b)). Inventory check: tests/unit/test_watcher_secret_propagation.py.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def _announce(*, ok: bool, detail: str = "") -> bool:
    """The timer runner owns failure counting; report the actual failing exit."""
    if not ok:
        print(_redact(f"{WATCH_NAME} error: {detail}")[:300])
    return False


def digest_card(message_id: str, emoji: str = "", *, scan: bool = False) -> int:
    """One digest card press, heard by the gateway — not a tick (no failure streak)."""
    _load_env_secrets()
    if not CLI.exists():
        print("mail skill is not mounted", file=sys.stderr)
        return 3
    result = subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
        [sys.executable, str(CLI), *(
            ["digest-reactions-only"] if scan else
            ["digest-react", "--message-id", message_id, *(["--emoji", emoji] if emoji else [])]
        )],
        capture_output=True, text=True, timeout=600, check=False,
        cwd=str(Path.home()),
        env={**os.environ, "AUTOPHAGY_REPO_ROOT": str(_runtime_root())},
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        print(_redact(tail[-1] if tail else f"rc={result.returncode}")[:300], file=sys.stderr)
    return result.returncode


def main() -> int:
    if sys.argv[1:] == ["--digest-reactions-only"]:
        return digest_card("", scan=True)
    if len(sys.argv) in (3, 5) and sys.argv[1] == "--digest-card":
        emoji = sys.argv[4] if len(sys.argv) == 5 and sys.argv[3] == "--emoji" else ""
        return digest_card(sys.argv[2], emoji)
    _load_env_secrets()
    if not CLI.exists():
        return 0 if _announce(ok=False, detail="mail skill is not mounted") else 1
    result = subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
        [sys.executable, str(CLI), "watch"],
        capture_output=True, text=True, timeout=1800, check=False,
        cwd=str(Path.home()),
        env={**os.environ, "AUTOPHAGY_REPO_ROOT": str(_runtime_root())},
    )
    if result.returncode == 0:
        _announce(ok=True)  # silent unless it closes an open incident
        return 0
    tail = (result.stderr or result.stdout).strip().splitlines()
    detail = tail[-1] if tail else f"rc={result.returncode}"
    recorded = _announce(ok=False, detail=f"rc={result.returncode}: {_redact(detail)[:200]}")
    return 0 if recorded else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked line
        print(f"mail-triage-watch error: {_redact(str(error))[:300]}")
        sys.exit(1)

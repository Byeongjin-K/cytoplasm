#!/usr/bin/env python3
"""Workday hourly MailOn collection; the timer runner owns failure counting.

Every tick returns the real collection result. Successful zero-new-mail results are
kept privately for doctor scheduled-jobs; weekends do not advance the quiet count.
The retired watch-failure ledger is neither read nor updated."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import watch_failure_streak
except ImportError:  # pragma: no cover — only reachable on a half-deployed node
    watch_failure_streak = None

WATCH_NAME: Final = "mail-sync-watch"
QUIET_NAME: Final = "mail-sync-quiet"
CLI: Final = Path("/srv/autophagy-skills/live/mail/scripts/mail_wrapper.py")
CLI_ENV: Final = "MAIL_SYNC_WRAPPER_CLI"
ALERT_AFTER_ENV: Final = "MAIL_SYNC_ALERT_AFTER"
QUIET_AFTER_ENV: Final = "MAIL_SYNC_QUIET_AFTER"
#: Most new mails one tick fetches; a backlog after an outage drains over a few ticks.
SYNC_LIMIT: Final = 50
#: Above the wrapper's own mailon timeout (MAIL_WRAPPER_TIMEOUT, default 900 s) plus its
#: SIGTERM grace, so the wrapper — not this tick — ends a slow sync.
TICK_TIMEOUT_SECONDS: Final = 1200
_ENV_SECRETS: Final = Path.home() / ".env.secrets"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron hands the wrapper no secrets; the mailon login needs them (규약 b)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        key, separator, value = raw.strip().partition("=")
        if separator and key and not key.startswith("#") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def _threshold(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "")))
    except ValueError:
        return default


def _cli() -> Path:
    override = os.environ.get(CLI_ENV, "").strip()
    return Path(override).expanduser() if override else CLI


def _payload(stdout: str) -> dict:
    for line in reversed(stdout.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _collect() -> tuple[bool, int | None, str]:
    """Run one sync: (collected, new mails or None when unknown, failure detail)."""
    cli = _cli()
    if not cli.exists():
        return False, None, "mail skill is not mounted"
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
            [sys.executable, str(cli), "list", "--sync", "--limit", str(SYNC_LIMIT)],
            capture_output=True, text=True, timeout=TICK_TIMEOUT_SECONDS, check=False,
            cwd=str(Path.home()), env=dict(os.environ),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, None, type(error).__name__
    payload = _payload(result.stdout)
    sync = payload.get("sync") if isinstance(payload.get("sync"), dict) else {}
    if result.returncode == 0 and sync.get("exit_code") == 0:
        new = sync.get("new_mails")
        return True, new if isinstance(new, int) else None, ""
    parts = [f"rc={result.returncode}"]
    for key in ("error_code", "failure_signature"):
        if payload.get(key):
            parts.append(f"{key}={payload[key]}")
    if sync.get("fallback"):
        parts.append(f"fallback={sync['fallback']}")
    return False, None, _redact(" ".join(parts))[:200]


def main() -> int:
    _load_env_secrets()
    collected, new, detail = _collect()
    _record_collection(Path.home(), collected, new, datetime.now().astimezone())
    if not collected:
        print(f"{WATCH_NAME} error: {detail}"[:300])
    return 0 if collected else 1


def _record_collection(home: Path, collected: bool, new: int | None, now: datetime) -> None:
    """Keep the result doctor's meaning check needs, not another failure incident."""
    path = home / ".hermes/mail-sync/collection.json"
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    streak = int(previous.get("zero_weekday_streak", 0))
    if now.astimezone(ZoneInfo("Asia/Seoul")).weekday() >= 5 or (collected and new != 0):
        streak = 0
    elif collected and new == 0:
        streak += 1
    payload = {"last_run_at": now.isoformat(), "last_status": "ok" if collected else "error",
               "new_mails": new, "zero_weekday_streak": streak}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked line
        print(f"{WATCH_NAME} error: {_redact(str(error))[:300]}")
        sys.exit(1)

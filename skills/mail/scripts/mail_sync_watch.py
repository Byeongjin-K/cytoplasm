#!/usr/bin/env python3
"""no_agent wrapper for `mail-sync-watch` — MailOn collection every 10 minutes, 07:00-24:00 KST.

2026-10-19 (owner 00:04 KST asked for every minute; one run takes a median 61 s with a fresh
login, so the lead chose 10 minutes): ticks never overlap a running collection (a busy lock
skips the tick), and ``--now`` is YooDori's on-request collection that waits for a running one.

Until 2026-10-13 the only scheduled collection was the sync inside the 08:00 KST digest.
That morning a DNS outage failed it, two ad-hoc syncs in the afternoon were killed by
the 900 s timeout, and nothing stored new institutional mail until the next morning —
while the owner and the project board, which reads the stored mail, were told nothing.

This tick runs the existing read path, ``mail_wrapper.py list --sync`` from the governed
live mount (subprocess only — importing the skill would re-enter the W3-2 cron-sandbox
PYTHONPATH trap), so a failed hour is retried by the next one and missed mail is picked
up by the normal incremental walk. Two failure streaks decide when to speak:

* ``mail-sync-watch`` — collection failed ``MAIL_SYNC_ALERT_AFTER`` ticks in a row
  (default 3 = about three workday hours with nothing stored). A local state.db
  fallback (wrapper sync exit 3) is a failed collection, not a success.
* ``mail-sync-quiet`` — collection succeeded but stored no new mail
  ``MAIL_SYNC_QUIET_AFTER`` workday ticks in a row (default 36 = six workday hours at 10
  minutes; only weekday 09:00-19:59 KST ticks count, so evenings and weekends never open it),
  which on a workday usually means the webmail list rendered empty.

Each streak speaks once when it opens and once when it recovers (watch_failure_streak);
the line goes to stdout, which Hermes ``--deliver discord`` delivers to the owner. A
recorded failure exits 0 (the scheduler would otherwise post its own banner every hour);
only a failure the streak could not record exits 1.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

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
LOCK_ENV: Final = "MAIL_SYNC_LOCK"
#: --now waits at most this long for a running collection before giving up.
NOW_WAIT_SECONDS: Final = 1500
KST: Final = timezone(timedelta(hours=9))
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


def _now() -> datetime:
    return datetime.now(KST)


def _workday(moment: datetime) -> bool:
    return moment.weekday() < 5 and 9 <= moment.hour < 20


def _lock(wait_seconds: float):
    """The one collection at a time: return the held lock file, or None when still busy."""
    # Outside ~/.hermes on purpose: that tree is being retired on the node (no new paths there).
    path = Path(os.environ.get(LOCK_ENV, "")
                or Path.home() / ".local/state/autophagy/mail-sync.lock").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                return None
            time.sleep(2)


def now() -> int:
    """YooDori's on-request collection: wait for a running one, then collect once."""
    _load_env_secrets()
    held = _lock(NOW_WAIT_SECONDS)
    if held is None:
        print("MAIL-SYNC-NOW busy: a collection is still running; try again shortly")
        return 1
    with held:
        collected, new, detail = _collect()
    if collected:
        print(f"MAIL-SYNC-NOW ok new={new if new is not None else '?'}")
        return 0
    print(f"MAIL-SYNC-NOW failed {detail}"[:300])
    return 1


def main() -> int:
    _load_env_secrets()
    held = _lock(0)
    if held is None:
        return 0  # a collection (an on-request one) is running; the next tick follows it
    with held:
        collected, new, detail = _collect()
    if watch_failure_streak is None:
        if collected:
            return 0
        print(f"{WATCH_NAME} error: {detail}"[:300])
        return 1
    notices = [watch_failure_streak.record(
        WATCH_NAME, ok=collected,
        detail=f"기관메일 수집 실패(10분 동기화) — {detail}",
        threshold=_threshold(ALERT_AFTER_ENV, 3),
    )]
    if collected and (new is None or new > 0 or _workday(_now())):
        notices.append(watch_failure_streak.record(
            QUIET_NAME, ok=new is None or new > 0,
            detail="기관메일 수집은 성공했지만 업무시간 동안 새로 저장된 메일 0건 — 웹메일 목록이 비어 보였는지 확인",
            threshold=_threshold(QUIET_AFTER_ENV, 36),
        ))
    for notice in notices:
        if notice is not None:
            print(notice[:300])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(now() if sys.argv[1:] == ["--now"] else main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked line
        print(f"{WATCH_NAME} error: {_redact(str(error))[:300]}")
        sys.exit(1)

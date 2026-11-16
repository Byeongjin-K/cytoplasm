"""no_agent wrapper for `mail-sync-watch` — MailOn collection every 10 minutes, 07:00-24:00 KST.

2026-10-19 (owner 00:04 KST asked for every minute; one run takes a median 61 s with a fresh
login, so the lead chose 10 minutes): ticks never overlap a running collection (a busy lock
skips the tick), and ``--now`` is YooDori's on-request collection that waits for a running one.

Every tick returns the real collection result (a failure prints one masked line and exits 1;
the timer runner owns failure counting). Successful zero-new-mail results are kept privately
in ``~/.local/state/autophagy/mail-sync/collection.json`` for doctor scheduled-jobs; only weekday 09:00-19:59
KST ticks advance that quiet count, so evenings and weekends never raise it. The retired
watch-failure ledger is neither read nor updated.

Issue #251: with the released ``mail-user-folders`` switch on, every collection also takes the
user folders (trash, spam and drafts never) of the last few days; off keeps the argv above.
``--now --user-folders [--since-days N]`` is the same scope asked through the YooDori gate.
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


def _folders_mode():
    """The switch module, found beside the wrapper in the live mail skill.

    The node runs this file as a lone copy in ~/.hermes/scripts (automation.cron_timer_run), so a mail
    module it imports is not beside it; v2.7.41 failed every tick with No module named
    'mail_user_folders_mode' (owner card Q60, 2026-11-16).
    """
    live = str(_cli().parent)
    if live not in sys.path:
        sys.path.append(live)
    import mail_user_folders_mode

    return mail_user_folders_mode


def _payload(stdout: str) -> dict:
    for line in reversed(stdout.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _scope() -> list[str]:
    """The periodic scope: the user folders only under the released switch."""
    mail_user_folders_mode = _folders_mode()

    return ["--user-folders"] if mail_user_folders_mode.mode() == "on" else []


def _now_scope(words: list[str]) -> list[str] | None:
    """``--now`` takes none, ``--user-folders`` or ``--user-folders --since-days N``; None refuses."""
    mail_user_folders_mode = _folders_mode()

    if not words:
        return _scope()
    if words == ["--user-folders"]:
        return words
    if (len(words) == 3 and words[:2] == ["--user-folders", "--since-days"] and words[2].isdigit()
            and 1 <= int(words[2]) <= mail_user_folders_mode.MAX_SINCE_DAYS):
        return [*words[:2], str(int(words[2]))]
    return None


def _collect(scope: list[str]) -> tuple[bool, int | None, str]:
    """Run one sync: (collected, new mails or None when unknown, failure detail)."""
    cli = _cli()
    if not cli.exists():
        return False, None, "mail skill is not mounted"
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
            [sys.executable, str(cli), "list", "--sync", "--limit", str(SYNC_LIMIT), *scope],
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


def now(words: list[str] | None = None) -> int:
    """YooDori's on-request collection: wait for a running one, then collect once."""
    scope = _now_scope(words or [])
    if scope is None:
        print("MAIL-SYNC-NOW refused: --now takes only --user-folders [--since-days 1-31]")
        return 2
    _load_env_secrets()
    held = _lock(NOW_WAIT_SECONDS)
    if held is None:
        print("MAIL-SYNC-NOW busy: a collection is still running; try again shortly")
        return 1
    with held:
        collected, new, detail = _collect(scope)
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
        collected, new, detail = _collect(_scope())
    _record_collection(Path.home(), collected, new, _now())
    if not collected:
        print(f"{WATCH_NAME} error: {detail}"[:300])
    return 0 if collected else 1


def _record_collection(home: Path, collected: bool, new: int | None, now: datetime) -> None:
    """Keep the result doctor's meaning check needs, not another failure incident."""
    path = home / ".local/state/autophagy/mail-sync/collection.json"
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    streak = int(previous.get("zero_weekday_streak", 0))
    local = now.astimezone(KST)
    if local.weekday() >= 5 or (collected and new != 0):
        streak = 0  # a weekend or new mail starts over; Friday's count never alarms on Monday
    elif collected and _workday(local):
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
        sys.exit(now(sys.argv[2:]) if sys.argv[1:2] == ["--now"] else main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked line
        print(f"{WATCH_NAME} error: {_redact(str(error))[:300]}")
        sys.exit(1)

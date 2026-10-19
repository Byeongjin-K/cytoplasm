#!/usr/bin/env python3
"""Reminder poller (W3-2) — Hermes cron job, no_agent script mode, every 5 min.

Reads cha's calendar through the W3-1 read-only list path (`gws calendar
events list`) and DMs cha for any event starting 55-65 minutes out; reads
~/state/milestones.yaml (W2-3) and DMs cha at D-3/D-1/D-day. Both legs are
idempotent through the SQLite claim-before-send store (reminder_store), so a
duplicate poller invocation sends zero duplicates.

The calendar is NEVER mutated here (list only). The DM is an internal
notification to cha's own DM — not an external effect, so no approval gate.

TZ: the host is UTC; all arithmetic is timezone-aware and milestone "today"
is the KST calendar date. The Hermes cron schedule itself follows the agent
config `timezone: Asia/Seoul` (W1-7 mechanism).

Deployed copy lives at ~/.hermes/scripts/poll_reminders.py (Hermes cron
sandbox rule) and imports the runtime generation that the link
~/.hermes/reminder_poller_runtime/.current points at. Force-run:

    hermes cron run <job-id>            # or directly:
    sudo -u agent -H python3 ~/.hermes/scripts/poll_reminders.py

no_agent semantics: empty stdout + exit 0 on success (silent tick); on
failure prints one masked line and exits 1 so the scheduler records an alert.

Env hooks (never set on the production cron path):
  REMINDER_DB               SQLite path (default ~/state/reminders.db)
  REMINDER_MILESTONES_FILE  milestones.yaml path (default ~/state/milestones.yaml)
  REMINDER_EVENTS_FILE      read events JSON from a file instead of gws
  REMINDER_CALENDAR         calendarId (default primary)
  REMINDER_GWS_BIN          gws binary override
  REMINDER_NOW              fixed ISO-8601 now (deterministic tests)
  REMINDER_DRY_RUN=1        print composed reminders instead of sending DMs
  REMINDER_CALENDAR_EVENTS  1/0 forces the calendar leg on or off (tests); otherwise the file below

Calendar leg switch (owner Q276, 2026-10-19 "유돌 것만 남기고 노드 일정 리마인더는 끄기 — 마일스톤 D-day
알림은 노드 그대로"): YooDori now announces upcoming events, so the calendar leg runs only when
~/.hermes/reminder-poller/calendar-events holds exactly "on". Milestones always run.
Undo on the node: printf on > ~/.hermes/reminder-poller/calendar-events
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

try:
    from automation.reminder_poller import poller_core, reminder_store
except ImportError:
    # Deployed layout: ~/.hermes/scripts/ + FLAT modules in the runtime dir.
    # Flat on purpose: the Hermes cron sandbox inherits
    # PYTHONPATH=~/.hermes/interop_runtime (W1-6 hooks), whose `automation`
    # package wins the import and gets cached in sys.modules — a nested
    # automation/reminder_poller runtime copy is unreachable there.
    sys.path.insert(0, os.path.realpath(Path.home() / ".hermes" / "reminder_poller_runtime" / ".current"))
    import poller_core  # pyright: ignore[reportMissingImports] # noqa: F401
    import reminder_store  # pyright: ignore[reportMissingImports] # noqa: F401

ENV_SECRETS = Path.home() / ".env.secrets"
GWS_TIMEOUT_S = 120
LOOKAHEAD = timedelta(minutes=90)


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def poll_now() -> datetime:
    override = os.environ.get("REMINDER_NOW", "")
    if override:
        return datetime.fromisoformat(override).astimezone(poller_core.KST)
    return datetime.now(poller_core.KST)


def gws_bin() -> str:
    override = os.environ.get("REMINDER_GWS_BIN", "")
    if override:
        return override
    import shutil

    found = shutil.which("gws") or os.path.expanduser("~/.local/bin/gws")
    if not Path(found).exists():
        raise RuntimeError("gws CLI not found (set REMINDER_GWS_BIN)")
    return found


def fetch_events(now: datetime) -> list[poller_core.CalendarEvent]:
    override = os.environ.get("REMINDER_EVENTS_FILE", "")
    if override:
        return poller_core.parse_events(Path(override).read_text(encoding="utf-8"))
    params = {
        "calendarId": os.environ.get("REMINDER_CALENDAR", "primary"),
        "maxResults": 50,
        "orderBy": "startTime",
        "singleEvents": True,
        "timeMax": (now + LOOKAHEAD).isoformat(),
        "timeMin": now.isoformat(),
    }
    result = subprocess.run(  # noqa: S603 — frozen read-only argv
        [gws_bin(), "calendar", "events", "list", "--params",
         json.dumps(params, ensure_ascii=False)],
        capture_output=True, text=True, timeout=GWS_TIMEOUT_S, check=False,
        cwd=str(Path.home()),  # gws writes empty responses to cwd files (W3-1 gotcha)
        env=dict(os.environ),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gws list failed rc={result.returncode}: {result.stderr.strip()[:200]}"
        )
    return poller_core.parse_events(result.stdout)


def read_milestones() -> list[dict[str, str]]:
    path = _env_path("REMINDER_MILESTONES_FILE", "~/state/milestones.yaml")
    if not path.exists():
        return []
    return poller_core.parse_milestones(path.read_text(encoding="utf-8"))


def bot_token() -> str:
    sys.path.insert(0, str(_release_runtime_root()))
    from automation.notice_bot import file_token, resolve_token

    token = resolve_token()
    if token:
        return token
    token = file_token(ENV_SECRETS)
    if token:
        return token
    raise RuntimeError("DISCORD_BOT_TOKEN not available")


def _release_runtime_root() -> Path:
    """owner_notice 파사드가 사는 릴리스 런타임 — 배포 사본이 늦게 해석한다(ON-2)."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override)
    release = Path("/srv/autophagy-agent-current")
    return release if release.is_dir() else Path("/srv/autophagy-agents")


@dataclass(frozen=True, slots=True)
class DmSender:
    """Owner-notice transport — 목적지(지정 채널/DM)는 owner_notice 파사드가 정한다(ON-2)."""

    context: poller_core.ReminderContext | None = None

    def send(self, body: str) -> None:
        if os.environ.get("REMINDER_DRY_RUN", "") == "1":
            print(f"DRY-RUN {body}")
            return
        os.environ.setdefault("DISCORD_BOT_TOKEN", bot_token())
        root = str(_release_runtime_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        from automation import owner_notice
        from automation.owner_notice import notify_owner

        message = poller_core.reminder_message(body, self.context)
        if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
            ok = notify_owner(body, message=message)
        else:
            ok = notify_owner(body)
        if not ok:
            # claim-before-send: raise → 호출자가 claim 을 release 해 다음 틱 재시도
            raise RuntimeError("owner notice delivery failed")


def _deliver(db: Path, sender: DmSender, kind: str, key: str, body: str) -> None:
    """Claim-before-send; a failed send releases the claim for retry."""
    if not reminder_store.claim(db, kind, key):
        return
    try:
        sender.send(body)
    except BaseException:
        reminder_store.release(db, kind, key)
        raise


def calendar_events_on() -> bool:
    forced = os.environ.get("REMINDER_CALENDAR_EVENTS", "")
    if forced in ("0", "1"):
        return forced == "1"
    try:
        return (Path.home() / ".hermes/reminder-poller/calendar-events").read_bytes().strip() == b"on"
    except OSError:
        return False


def main() -> int:
    now = poll_now()
    db = _env_path("REMINDER_DB", "~/state/reminders.db")
    for event in fetch_events(now) if calendar_events_on() else ():
        if not poller_core.in_reminder_window(event.start, now):
            continue
        sender = DmSender(poller_core.ReminderContext(
            key=poller_core.event_key(event), source=("일정 검색", event.event_id),
            url=event.source_url, window=(now, now + LOOKAHEAD),
        ))
        _deliver(
            db, sender, "event", poller_core.event_key(event),
            poller_core.compose_event_reminder(event, now),
        )
    today = now.date()
    for entry in read_milestones():
        offset = poller_core.milestone_offset(entry.get("deadline", ""), today)
        if offset is None:
            continue
        source = entry.get("source") or poller_core.milestone_key(entry, offset)
        sender = DmSender(poller_core.ReminderContext(
            key=poller_core.milestone_key(entry, offset), source=("마일스톤 출처", source),
            url=source if source.startswith(("https://", "http://")) else None,
        ))
        _deliver(
            db, sender, "milestone", poller_core.milestone_key(entry, offset),
            poller_core.compose_milestone_reminder(entry, offset),
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked line
        print(f"reminder-poller error: {poller_core.redact(str(error))[:300]}")
        sys.exit(1)

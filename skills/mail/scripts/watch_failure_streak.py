"""Historical failure-ledger replay; live wrappers retired this path in Q251.

The timer runner and doctor now own live failure observation. Existing files and this
pure replay API remain for historical evidence, not for scheduled writers or readers.

WHY (2026-08-18): `mail-triage-watch` failed 111 ticks in a row and calendar/coordination
222 each, and cha learned about none of them — those jobs run `--deliver local`, which has
zero delivery targets, so the failure line went nowhere. The obvious fix (`--deliver
discord`) is worse for a high-frequency job: a stuck `*/2` watcher would send 720 DMs a day.

So the delivery target is not the knob. The knob is *how often a watcher speaks*: stay
silent while healthy, speak exactly once when a failure streak reaches the threshold, and
speak exactly once more when it recovers. That is the shape `automation/deploy_reconcile.py`
already uses (`FAILURE_NOTICE_THRESHOLD` + `incident_open`), reused here rather than
reinvented — with the state kept per watcher name so two watchers never share an incident.

WHY (t_2b8f1ab9 / recorded_failure_detail_unavailable_after_recovery): closing the streak
used to drop the redacted failing-tick line, so the recovery Discord notice said only
"recovered after N consecutive failures" and nothing anywhere still named WHAT failed.
The state now keeps a last_failure summary (already-redacted detail ≤200, first/last
failed-at as UTC ISO, count) across that reset, and the recovery line carries the window
and last failure so the owner can see the cause without digging. Old files without those
keys still load.

Deployed alongside the watchers in ``~/.hermes/scripts/`` and imported from there; it is a
helper, not a cron entrypoint (no ``__main__`` guard), so the deploy-coverage probe skips it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

#: A `*/10` watcher reaches this in under an hour — long enough to ride out a transient
#: node hiccup, short enough that the owner hears about a real outage the same morning.
DEFAULT_THRESHOLD: Final = 5

#: Resolved per call, and overridable through ``WATCH_FAILURE_ROOT``, so a test never
#: writes into the live owner state and a second account never shares the first's.
STATE_ROOT_ENV: Final = "WATCH_FAILURE_ROOT"

#: Returned when a state transition could not be saved, so a watcher can keep its
#: failure visible instead of silently treating the tick as recorded.
PERSISTENCE_FAILURE: Final = "failure streak state was not persisted"

#: Stored last-failure detail budget — callers already redact; this is the on-disk cap.
DETAIL_MAX: Final = 200

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


def default_root() -> Path:
    override = os.environ.get(STATE_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes" / "watch-failure"


@dataclass(frozen=True, slots=True)
class LastFailure:
    """Redacted summary of the streak that just closed — survives the counter reset."""

    detail: str = ""
    first_failed_at: str = ""
    last_failed_at: str = ""
    count: int = 0


@dataclass(frozen=True, slots=True)
class Streak:
    """What the store remembers between ticks."""

    consecutive_failures: int = 0
    incident_open: bool = False
    last_failure: LastFailure | None = None


def state_path(name: str, root: Path | None = None) -> Path:
    return (root or default_root()) / f"{name}.json"


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(name: str, root: Path | None = None) -> Streak:
    """Read the streak; unreadable or corrupt state restarts at zero rather than crashing."""
    try:
        raw: JsonValue = json.loads(state_path(name, root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Streak()
    if not isinstance(raw, dict):
        return Streak()
    failures = raw.get("consecutive_failures")
    summary = raw.get("last_failure")
    last_failure = None
    if isinstance(summary, dict):
        detail = summary.get("detail")
        first = summary.get("first_failed_at")
        last = summary.get("last_failed_at")
        count = summary.get("count")
        last_failure = LastFailure(
            detail=detail[:DETAIL_MAX] if isinstance(detail, str) else "",
            first_failed_at=first if isinstance(first, str) else "",
            last_failed_at=last if isinstance(last, str) else "",
            count=count if isinstance(count, int) and count >= 0 else 0,
        )
    return Streak(
        consecutive_failures=failures if isinstance(failures, int) and failures >= 0 else 0,
        incident_open=bool(raw.get("incident_open", False)),
        last_failure=last_failure,
    )


def store(name: str, streak: Streak, root: Path | None = None) -> bool:
    """Persist the streak 0600 under a private directory, returning whether it succeeded."""
    path = state_path(name, root)
    payload: dict[str, JsonValue] = {
        "consecutive_failures": streak.consecutive_failures,
        "incident_open": streak.incident_open,
    }
    if streak.last_failure is not None:
        payload["last_failure"] = {
            "count": streak.last_failure.count,
            "detail": streak.last_failure.detail,
            "first_failed_at": streak.last_failure.first_failed_at,
            "last_failed_at": streak.last_failure.last_failed_at,
        }
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
    except OSError:
        return False
    return True


def record(
    name: str,
    *,
    ok: bool,
    detail: str = "",
    threshold: int = DEFAULT_THRESHOLD,
    root: Path | None = None,
) -> str | None:
    """Advance the streak and return a notice, silence, or a persistence-failure marker.

    A successful tick after an open incident closes it and returns the recovery line; a
    failing tick returns the escalation line only on the tick that reaches ``threshold``.
    Every other tick returns ``None``, which is what keeps a stuck `*/2` job from filling
    the owner's DMs while still letting the job run under ``--deliver discord``. A failed
    write returns ``PERSISTENCE_FAILURE`` so callers do not mistake an unrecorded failure
    for a recorded silent tick. The recovery line carries the last-failure window and
    redacted detail so closing the streak does not erase what failed.
    """
    previous = load(name, root)
    if ok:
        if previous.consecutive_failures == 0 and not previous.incident_open:
            return None  # healthy (or already-recovered) writes nothing
        if not store(name, Streak(last_failure=previous.last_failure), root):
            return PERSISTENCE_FAILURE
        if not previous.incident_open:
            return None
        line = f"{name} recovered after {previous.consecutive_failures} consecutive failures"
        failure = previous.last_failure
        if failure is not None and failure.first_failed_at and failure.last_failed_at:
            line += f" ({failure.first_failed_at}..{failure.last_failed_at} UTC)"
        if failure is not None and failure.detail:
            line += f" — last failure: {failure.detail}"
        return line

    failures = previous.consecutive_failures + 1
    now = _utc_stamp()
    prior = previous.last_failure
    if previous.consecutive_failures == 0 or prior is None or not prior.first_failed_at:
        first = now
    else:
        first = prior.first_failed_at
    last_failure = LastFailure(
        detail=detail[:DETAIL_MAX],
        first_failed_at=first,
        last_failed_at=now,
        count=failures,
    )
    reached = failures >= threshold and not previous.incident_open
    if not store(
        name,
        Streak(
            consecutive_failures=failures,
            incident_open=previous.incident_open or reached,
            last_failure=last_failure,
        ),
        root,
    ):
        return PERSISTENCE_FAILURE
    if not reached:
        return None
    suffix = f": {detail}" if detail else ""
    return f"{name} failed {failures} ticks in a row{suffix}"

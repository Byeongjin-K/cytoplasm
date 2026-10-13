#!/usr/bin/env python3
"""no_agent wrapper for Hermes cron `mail-daily-digest` (08:00 KST) that sends
the owner mail digest; silent on success.

Thin wrapper: runs the mounted mail skill CLI `digest` subcommand. no_agent
semantics: empty stdout + exit 0 on success (silent tick); on failure prints one
masked line and exits 1 so the scheduler records an alert. Deployed copy lives
at ~/.hermes/scripts/mail_digest_watch.py (Hermes cron sandbox rule); the skill
CLI stays the single implementation in the immutable governed live store at
/srv/autophagy-skills/live/mail/scripts/ — no import of it here, subprocess
only (avoids the W3-2 cron-sandbox PYTHONPATH package-shadowing trap).

The CLI and this wrapper speak one contract: every digest failure surfaces as
exactly one structured ``DIGEST-FAIL stage=... retry_safe=... code=...`` line.
This wrapper passes a child marker through verbatim, or synthesizes a
``stage=runner`` marker when the child produced none (crash, non-marker exit,
or unmounted skill). In-tick retry fires ONLY for ``retry_safe=true`` markers;
a ``retry_safe=false`` failure (a delivery that may already have sent some
Discord chunks, or a build item that may already have delegated a calendar
draft) is never auto-replayed, so the owner never receives a duplicate.

The marker is written to STDOUT: Hermes ``--no-agent`` delivers the script's
stdout verbatim to the cron ``--deliver`` owner-DM target, and drops stderr
(verified in the 2026-07-31 saved cron output). Success is an empty stdout
(silent tick); a failure is the one marker line + exit 1, which the cron
delivers to cha — rather than the app re-sending a DM through the failing path.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

CLI = Path("/srv/autophagy-skills/live/mail/scripts/triage_cli.py")
_ENV_SECRETS = Path.home() / ".env.secrets"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")
_MARKER = re.compile(r"DIGEST-FAIL stage=\S+ retry_safe=(true|false) code=\S+")
_RETRY_DELAYS_S = (20, 60, 120)

# Deployed flat beside this wrapper (~/.hermes/scripts/) and sitting beside it in the
# repo, so one path insert covers both; a half-deployed node must not kill the tick.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import watch_failure_streak
except ImportError:  # pragma: no cover — only reachable on a half-deployed node
    watch_failure_streak = None

WATCH_NAME = "mail-daily-digest"
#: Separate incident stream: a stale vendor runtime is not a digest failure.
DRIFT_WATCH_NAME = "mailon-runtime-drift"



def _runtime_root() -> Path:
    """Where the ``automation`` package lives (DG-4 order).

    The child resolves ``automation.interop`` for the approval façade. From the
    mounted skill its own guess lands on ``/srv/autophagy-skills/releases``, which
    carries no automation package, and the adapter then refuses the request
    (``GATE-REFUSED … 승인 게시 거부``). Measured 2026-08-18: the triage watcher passed
    this and ran clean while the digest watcher did not and died on every tick.
    Per the no-agent cron rule (b-2) the parent states the root explicitly.
    """
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if (current / "automation").is_dir() else Path("/srv/autophagy-agents")


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron hands the wrapper no secrets, so the parent loads them itself.

    Measured 2026-08-18 on `budget-watch`: the value was present in ~/.env.secrets and
    the tick still died as if it were missing, because nothing ever put it in the
    environment — `set -a; . ~/.env.secrets` then the same wrapper exited 0. Every
    watcher deployed to ~/.hermes/scripts/ owes the same contract (규약 (b)); the
    inventory check is tests/unit/test_watcher_secret_propagation.py.
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


def _run_digest() -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
        [sys.executable, str(CLI), "digest"],
        capture_output=True, text=True, timeout=1800, check=False,
        cwd=str(Path.home()),
        env={**os.environ, "AUTOPHAGY_REPO_ROOT": str(_runtime_root())},
    )


def _child_marker(output: str) -> str | None:
    """Return the child's single structured DIGEST-FAIL marker line, if any.

    triage_cli prints every GateError as ``GATE-REFUSED <marker>``; anchoring at the
    line start missed every real marker (2026-10-13 08:11: a DNS failure was reported
    as ``stage=runner code=child_exit`` and a retry-safe one would never be retried).
    """
    for line in output.splitlines():
        found = _MARKER.search(line)
        if found:
            return line[found.start():].strip()
    return None


def _is_retry_safe(marker: str) -> bool:
    """True only when the child explicitly declared the failure safe to replay."""
    return "retry_safe=true" in marker


def _note_outcome(*, ok: bool, detail: str = "") -> None:
    """Track the digest's failure streak so a recovery is announced exactly once.

    The failure side stays untouched: the DIGEST-FAIL marker is this watcher's contract
    with the cron and with tests/unit/test_mail_digest_watch_retry.py, so the escalation
    line the streak returns on a failing tick is deliberately dropped. What was missing
    was the other half of the pattern — nothing ever told cha the digest started working
    again, so a fixed outage looked identical to an outage nobody had noticed.
    """
    if watch_failure_streak is None:
        return
    notice = watch_failure_streak.record(WATCH_NAME, ok=ok, detail=detail, threshold=1)
    if ok and notice is not None:
        print(notice[:300])


def _report_runtime_drift() -> None:
    """Say once when the node's mailon runtime is not the vendor code origin/main carries.

    2026-07-29 -> 08-18: a committed vendor fix took **19 days** to reach production and
    nothing said so — skills are judged by `readlink live/<skill>` and code converges
    through the reconciler, but the vendor runtime is neither. The daily digest is the
    one agent-owned job that already delivers to the owner, so the probe rides it. The
    streak store (threshold 1) keeps a long-standing drift to one notice plus one when
    it clears, instead of a line every morning.
    """
    probe = Path(__file__).resolve().parent / "mailon_runtime_drift.sh"
    if not probe.exists() or watch_failure_streak is None:
        return  # a half-deployed node must not turn a working digest into a failure
    try:
        outcome = subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
            ["/bin/bash", str(probe)],
            capture_output=True, text=True, timeout=120, check=False,
            cwd=str(Path.home()),
            # The same runtime root the digest child gets: the probe reads it as the
            # release tree whose vendor digest the deployed runtime must match (규약 b-2).
            env={**os.environ, "AUTOPHAGY_REPO_ROOT": str(_runtime_root())},
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if outcome.returncode == 2:
        return  # undecidable is not an incident to announce; it is a gap to fix on the node
    detail = (outcome.stdout or "").strip().splitlines()
    notice = watch_failure_streak.record(
        DRIFT_WATCH_NAME, ok=outcome.returncode == 0,
        detail=_redact(detail[-1]) if detail else "", threshold=1,
    )
    if notice is not None:
        print(notice[:300])


def main() -> int:
    _load_env_secrets()
    if not CLI.exists():
        _note_outcome(ok=False, detail="mail skill is not mounted")
        print("DIGEST-FAIL stage=runner retry_safe=false code=not_mounted detail=mail skill is not mounted")
        return 1
    result = _run_digest()
    for delay in _RETRY_DELAYS_S:
        marker = _child_marker(result.stderr or result.stdout)
        if result.returncode == 0 or marker is None or not _is_retry_safe(marker):
            break
        time.sleep(delay)
        result = _run_digest()
    _report_runtime_drift()
    if result.returncode == 0:
        _note_outcome(ok=True)
        return 0
    marker = _child_marker(result.stderr or result.stdout)
    if marker is not None:
        detail = _redact(marker)
        _note_outcome(ok=False, detail=detail)
        print(detail[:300])
    else:
        tail = (result.stderr or result.stdout).strip().splitlines()
        detail = _redact(tail[-1]) if tail else ""
        _note_outcome(ok=False, detail=detail or f"rc={result.returncode}")
        print(
            f"DIGEST-FAIL stage=runner retry_safe=false code=child_exit "
            f"child_rc={result.returncode} detail={detail[:200]}"
        )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked marker line
        print(
            f"DIGEST-FAIL stage=runner retry_safe=false code=wrapper_crash detail={_redact(str(error))[:200]}"
        )
        sys.exit(1)

#!/usr/bin/env python3
"""W3-6 bank actuator for the w3-calendar scenario — runs ON agent@<primary-node>.

Cases (observe-only; the local judge compares against the scenario YAML):
  register_delete   W3-1 gated create (signed injection) -> real gws event ->
                    W3-1 gated delete restores the baseline.
  auth_401_error_report  CALENDAR_GWS_BIN points at a 401 shim: the owner-
                    confirmed execution fails, and the failure must surface on
                    the documented error path (exit 6 + GATE-REFUSED stderr +
                    `failed` audit record) with ZERO real-calendar side effect.

Isolation: CALENDAR_GATE_DIR is a per-run temp dir (no draft residue); the 401
case additionally isolates CALENDAR_APPROVAL_LOG and the gws binary. The real
approvals.jsonl only grows by the register/delete audit records (by design).
E2E_TEST_MODE=1 + INTEROP_E2E_SECRET exist only in this process tree — the
production gateway keeps refusing E2E mode at boot (W1-6 guard).

Emits exactly one `OBS-JSON: {...}` line; all other output is masked progress.
Usage: w3_calendar_remote.py --secret-file <path>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CLI = "/srv/autophagy-skills/live/calendar/scripts/calendar_cli.py"
REAL_APPROVALS = Path("/srv/autophagy-agents/logs/approvals.jsonl")
MARKER = "W36-BANK"
SUMMARY = f"{MARKER} 캘린더 리마인드 테스트"
MARKER_401 = "W36-BANK-401"


def run(argv: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(  # noqa: S603 — fixed local binaries under test
        argv, capture_output=True, text=True, timeout=180, env=env, check=False
    )


def cli(args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return run([sys.executable, CLI, *args], env_extra)


def listed_count(query: str) -> int:
    """Real gws read (never through any shim/gate override)."""
    result = cli(["list", "--days", "3", "--query", query])
    matched = re.search(r"^LISTED n=(\d+)", result.stdout, re.M)
    return -1 if matched is None else int(matched.group(1))


def approvals_lines() -> int:
    return len(REAL_APPROVALS.read_text(encoding="utf-8").splitlines())


def gated(
    draft_args: list[str], gate_env: dict[str, str], work: Path
) -> tuple[subprocess.CompletedProcess, subprocess.CompletedProcess | None]:
    """W3-1 unattended path: draft -> sign (HMAC injection) -> confirm."""
    draft = cli(draft_args, gate_env)
    if draft.returncode != 0:
        return draft, None
    matched = re.search(r"DRAFT-CREATED id=([0-9a-f]+)", draft.stdout)
    if matched is None:
        raise RuntimeError("draft id not found in draft output")
    draft_id = matched.group(1)
    injection = work / f"inj-{draft_id}.json"
    sign = cli(["sign", "--draft", draft_id, "--out", str(injection)], gate_env)
    if sign.returncode != 0:
        raise RuntimeError(f"sign failed rc={sign.returncode}")
    confirm = cli(["confirm", "--draft", draft_id, "--injection-file", str(injection)], gate_env)
    injection.unlink(missing_ok=True)
    return draft, confirm


def case_register_delete(work: Path) -> dict:
    obs: dict[str, object] = {"error": None}
    gate_env = {"CALENDAR_GATE_DIR": str(work / "gate")}
    base_events = listed_count(MARKER)
    base_appr = approvals_lines()
    obs["baseline_events"] = base_events

    draft, confirm = gated(
        ["draft-create", "--text", "내일 오전 9시 뱅크 회의", "--summary", SUMMARY], gate_env, work
    )
    obs["create_exit"] = draft.returncode
    obs["confirm_create_exit"] = -1 if confirm is None else confirm.returncode
    executed = re.search(r"EXECUTED action=create event=(\S+)", confirm.stdout if confirm else "")
    event_id = executed.group(1) if executed else ""
    obs["executed_create"] = bool(event_id and event_id != "-")
    obs["events_delta_after_create"] = listed_count(MARKER) - base_events
    obs["approvals_delta_after_create"] = approvals_lines() - base_appr

    _, delete_confirm = gated(
        ["draft-delete", "--event-id", event_id, "--label", SUMMARY], gate_env, work
    )
    obs["confirm_delete_exit"] = -1 if delete_confirm is None else delete_confirm.returncode
    obs["events_after_cleanup_equals_baseline"] = listed_count(MARKER) == base_events
    obs["approvals_total_delta"] = approvals_lines() - base_appr
    return obs


def case_auth_401(work: Path) -> dict:
    obs: dict[str, object] = {"error": None}
    iso = work / "iso-401"
    iso.mkdir(mode=0o700)
    calls = iso / "shim-calls.log"
    shim = iso / "gws-401-shim.sh"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        "echo 'Error: googleapi 401 Unauthorized: Request had invalid"
        " authentication credentials (UNAUTHENTICATED)' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    shim.chmod(0o700)
    iso_log = iso / "approvals.jsonl"
    gate_env = {
        "CALENDAR_GATE_DIR": str(iso / "gate"),
        "CALENDAR_APPROVAL_LOG": str(iso_log),
        "CALENDAR_GWS_BIN": str(shim),
    }
    base_real_appr = approvals_lines()

    draft, confirm = gated(
        ["draft-create", "--text", "내일 오전 10시 뱅크 회의", "--summary", f"{MARKER_401} 주입 테스트"],
        gate_env,
        work,
    )
    obs["draft_exit"] = draft.returncode
    obs["confirm_exit"] = -1 if confirm is None else confirm.returncode
    stderr = confirm.stderr if confirm else ""
    obs["stderr_has_gate_refused"] = "GATE-REFUSED" in stderr
    obs["stderr_has_401"] = "401" in stderr
    obs["shim_called"] = calls.exists()
    obs["shim_saw_insert"] = calls.exists() and "calendar events insert" in calls.read_text()

    records = (
        [json.loads(line) for line in iso_log.read_text(encoding="utf-8").splitlines()]
        if iso_log.exists()
        else []
    )
    failed = [r for r in records if r.get("result", {}).get("status") == "failed"]
    obs["isolated_approval_records"] = len(records)
    obs["audit_failed_recorded"] = len(failed) == 1
    obs["audit_action"] = failed[0]["action"] if failed else ""
    obs["real_calendar_leak"] = listed_count(MARKER_401) != 0
    obs["real_approvals_delta"] = approvals_lines() - base_real_appr
    return obs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-file", required=True)
    args = parser.parse_args()
    os.environ["E2E_TEST_MODE"] = "1"
    os.environ["INTEROP_E2E_SECRET"] = Path(args.secret_file).read_text(encoding="utf-8").strip()

    observations: dict[str, dict] = {}
    work = Path(tempfile.mkdtemp(prefix=".w36-calendar-", dir=Path.home()))
    try:
        for case_id, case_fn in (
            ("register_delete", case_register_delete),
            ("auth_401_error_report", case_auth_401),
        ):
            try:
                observations[case_id] = case_fn(work)
            except Exception as error:  # noqa: BLE001 — observed, judged, isolated
                observations[case_id] = {"error": f"{type(error).__name__}: {error}"[:200]}
            print(f"W36 case={case_id} error={observations[case_id].get('error')}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("OBS-JSON: " + json.dumps(observations, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

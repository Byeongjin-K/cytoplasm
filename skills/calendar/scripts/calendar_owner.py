"""Q233 owner-command execution; outside invitations keep the existing card."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import calendar_confirm
import calendar_gate
import calendar_output
import calendar_preflight
from calendar_confirm_input import DraftRecord


def _outside_attendees(draft: DraftRecord) -> bool:
    """Compare frozen invite addresses to the authenticated owner's primary calendar."""
    argv = draft["argv"]
    body = json.loads(argv[argv.index("--json") + 1])
    attendees = body.get("attendees", [])
    if not attendees:
        return False
    # Resolve the authenticated account, not the destination calendar (which may
    # be shared). Unknown identities must keep the approval card, never bypass it.
    result = subprocess.run(  # noqa: S603
        [calendar_gate.gws_bin(), "calendar", "calendars", "get",
         "--params", json.dumps({"calendarId": "primary"})],
        capture_output=True, text=True, timeout=calendar_gate.GWS_TIMEOUT_S,
        check=False, cwd=str(Path.home()),
    )
    if result.returncode:
        return True
    try:
        owner_email = json.loads(result.stdout).get("id", "")
    except json.JSONDecodeError:
        return True
    if not isinstance(owner_email, str) or not owner_email:
        return True
    return any(
        not isinstance(attendee, dict)
        or not isinstance(attendee.get("email"), str)
        or attendee["email"].casefold() != owner_email.casefold()
        for attendee in attendees
    )


def create(draft: DraftRecord) -> int:
    """Execute a solo command once; only verified success emits the ledger marker."""
    channel = str(draft.get("origin_channel_id") or "")
    message = str(draft.get("origin_message_id") or "")
    if not channel.isdecimal() or not message.isdecimal():
        raise calendar_gate.GateError("소유자 명령의 지시 채널·메시지 id가 필요합니다", 2)
    if _outside_attendees(draft):
        calendar_gate.persist_draft(draft)
        result = calendar_output.cmd_post_confirm(argparse.Namespace(draft=draft["id"]))
        if result == 0:
            print(f"CARD-POSTED draft={draft['id']}")
        return result
    approval = calendar_gate.Approval(
        ref=f"owner-command:{channel}:{message}",
        method="owner_command", owner=calendar_confirm.owner_id(),
    )
    # Never expose an uncarded pending draft to the orphan-card watcher while a
    # write is running or its result is uncertain. execute_draft seals success.
    draft = {**draft, "status": "executing"}
    calendar_gate.persist_draft(draft)
    try:
        calendar_preflight.guarded_execute_draft(draft, approval)
    except calendar_preflight.CalendarPreflightError as error:
        raise calendar_gate.GateError(str(error), error.exit_code) from error
    calendar_confirm.clear_pending(str(draft["id"]))
    print(f"OWNER-COMMAND-WRITTEN calendar {draft['id']} {draft['action']}")
    return 0

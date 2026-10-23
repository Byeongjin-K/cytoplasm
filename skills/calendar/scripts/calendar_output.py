from __future__ import annotations

import argparse
import sys
from importlib import import_module

import calendar_core
import calendar_gate


def print_draft(record: dict[str, str | list[str]]) -> None:
    print(
        calendar_core.render_change_summary(
            action=str(record["action"]),
            summary=str(record["summary"]),
            start=str(record["start"]),
            end=str(record["end"]),
            calendar_id=str(record["calendar_id"]),
            event_id=str(record["event_id"]),
            all_day=calendar_core.argv_all_day(record["argv"]),
        )
    )
    print(f"DRAFT-CREATED id={record['id']} action={record['action']} sha256={record['sha256']}")
    print(
        f"실행하려면 DM으로 `실행 {record['id']}` 라고 답장하세요. "
        f"취소는 `취소 {record['id']}`."
    )


def cmd_list_drafts(_args: argparse.Namespace) -> int:
    for record in calendar_gate.list_drafts():
        print(
            f"DRAFT id={record['id']} status={record['status']} action={record['action']} "
            f"created={record['created']}"
        )
    return 0


def cmd_post_confirm(args: argparse.Namespace) -> int:
    draft = calendar_gate.load_draft(args.draft)
    calendar_approval = import_module("calendar_approval")
    entry = calendar_approval.request_confirmation(draft)
    print(f"PENDING-OWNER draft={draft['id']} message={entry.dm_message_id}")
    _print_thread_line(calendar_gate.load_draft(draft["id"]))
    return 0


def _print_thread_line(draft: dict[str, object]) -> None:
    try:
        from automation.interop.thread_pointer import approval_thread_line
    except ImportError as error:
        print(f"APPROVAL-THREAD-UNAVAILABLE draft={draft['id']} err={type(error).__name__}",
              file=sys.stderr)
        return
    print(approval_thread_line("draft", str(draft["id"]), draft))

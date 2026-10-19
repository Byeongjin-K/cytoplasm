"""Calendar-only digest snapshots for the shared digest settle policy.

Mail-owned threads are deliberately absent: opening a calendar-only digest does not
mean a newer mail digest exists. These threads only carry calendar cards.
"""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import calendar_binding
import calendar_gate

if TYPE_CHECKING:
    from automation.interop.digest_thread import DigestThread


def settle() -> None:
    """Best-effort marks; unknown cards never permit archival."""
    import calendar_confirm
    import calendar_digest

    try:
        shared = calendar_binding._repo_module("digest_thread")
        directory = calendar_gate._gate_dir() / "daily-threads"
        threads = []
        for path in sorted(directory.glob("*.json"))[-shared.KEEP_DAYS:]:
            record = json.loads(path.read_text(encoding="utf-8"))
            name = record.get("name")
            if not name:
                surface = calendar_binding._surface()
                name = surface.request_thread_name(
                    surface.ApprovalKind.CALENDAR, surface.RequestThread(title=f"다이제스트 {path.stem}"))
            threads.append(shared.DigestThread(
                day=path.stem, channel_id=record["channel_id"], surface=record["surface"],
                policy_version=record["policy_version"], guild_id=record.get("approval_guild_id"),
                name=name, mark=record.get("mark", shared.MARK_NONE),
            ))

        def save(thread: DigestThread) -> None:
            path = directory / f"{thread.day}.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["channel_id"] == thread.channel_id:
                calendar_gate.write_json(path, {**record, "mark": thread.mark})

        try:
            counts = calendar_digest.pending_by_thread([thread.channel_id for thread in threads])
        except Exception:  # noqa: BLE001 — unreadable pending store is unknown, never zero
            counts = {}
        shared.settle(counts, calendar_confirm._api, store=shared.DigestStore(tuple(threads), save))
    except Exception as error:  # noqa: BLE001 — marks must not break approval publication
        print(f"DIGEST-THREAD-SETTLE-FAIL kind=calendar err={type(error).__name__}", file=sys.stderr)

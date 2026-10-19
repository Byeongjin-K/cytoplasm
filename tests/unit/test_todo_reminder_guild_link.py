from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
from pathlib import Path

from automation.interop.approval_lease import FileKeyLease
from automation.interop.approval_reminder_config import ApprovalReminderConfig

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "todo" / "scripts"))


def test_todo_reminder_links_original_card_with_guild_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a pending todo approval card in a server thread.
    base = import_module("test_todo_watch")
    monkeypatch.setattr(base, "_CHANNEL", "1500000000000000001")
    monkeypatch.setattr(base, "_MESSAGE", "1500000000000000003")
    item = base._fixture(tmp_path)
    posted: list[tuple[str, str]] = []

    class Transport:
        def post_message(self, channel_id: str, content: str) -> str:
            posted.append((channel_id, content))
            return "1500000000000000004"

        def get_message(self, channel_id: str, message_id: str) -> str | None:
            return item.transport.get_message(channel_id, message_id)

        def get_reaction_users(self, channel_id: str, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
            return item.transport.get_reaction_users(channel_id, message_id, emoji)

        def fetch_channel(self, channel_id: str) -> dict[str, str | int]:
            assert channel_id == "1500000000000000001"
            return {"id": channel_id, "type": 11, "guild_id": "1500000000000000009"}

    # When: the watcher sends a due reminder through its real wiring.
    watch = import_module("todo_confirm_reaction_watch")
    watch.run_once(
        store=item.store,
        owner_id="owner-fixture",
        transport=Transport(),
        directory=item.directory,
        approval_log=item.log,
        lease=FileKeyLease(tmp_path / "state" / "approval-leases"),
        now=datetime(2026, 8, 16, 12, 30, tzinfo=UTC),
        reminder_config=ApprovalReminderConfig(initial_delay=timedelta(minutes=1), repeat_interval=timedelta(hours=1)),
    )

    # Then: the original approval card link names its guild, not @me.
    assert posted
    assert "https://discord.com/channels/1500000000000000009/1500000000000000001/1500000000000000003" in posted[0][1]
    assert "@me" not in posted[0][1]

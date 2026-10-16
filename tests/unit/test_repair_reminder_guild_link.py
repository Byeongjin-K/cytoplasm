from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.interop import owner_message
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ApprovalSurface, ChannelFacts, RequestThread
from automation.repair.repair_ops_approval import repair_action_hash
from automation.repair.repair_ops_discord import RepairDiscordApi
from automation.repair.repair_ops_pending import PendingRepairApproval, PendingRepairApprovalStore, approval_request_content
from automation.repair.repair_ops_reaction_watch import RepairApprovalWatcher


OWNER_ID = "1500000000000000010"
CHANNEL_ID = "1500000000000000001"
MESSAGE_ID = "1500000000000000003"
GUILD_ID = "1500000000000000009"
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
BINDING = ApprovalBinding(ApprovalKind.REPAIR, ApprovalSurface.SKILL_APPROVALS, CHANNEL_ID, 4)


class _Directory:
    def owner_dm(self) -> str:
        return CHANNEL_ID

    def skill_approvals(self) -> str:
        return CHANNEL_ID

    def agent_chat(self) -> str:
        return CHANNEL_ID

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        del kind
        return CHANNEL_ID

    def agent_chat_request_thread(self, kind: ApprovalKind, request: RequestThread) -> str:
        del kind, request
        return CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        return ChannelFacts(11, channel_id, (), CHANNEL_ID)


class _Commands:
    def apply(self, pending: PendingRepairApproval) -> bool:
        del pending
        return True

    def discard(self, pending: PendingRepairApproval, reason: str) -> bool:
        del pending, reason
        return True


def test_repair_watcher_reminder_links_original_card_with_guild_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a due repair approval in a thread, backed by the real Discord transport.
    pending = PendingRepairApproval(
        "repair-1", "patch.diff", repair_action_hash("repair-1", "patch.diff"),
        "nonce", MESSAGE_ID, NOW - timedelta(hours=1),
        kind=ApprovalKind.REPAIR, surface=ApprovalSurface.SKILL_APPROVALS,
        channel_id=CHANNEL_ID, policy_version=4,
    )
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(pending)
    posts: list[tuple[str, str]] = []

    api = RepairDiscordApi("token", BINDING, directory=_Directory(), owner_id=OWNER_ID)
    def same_api(_self: RepairDiscordApi, _record: PendingRepairApproval) -> RepairDiscordApi:
        return api

    monkeypatch.setattr(RepairDiscordApi, "for_pending", same_api)

    def fake_api(_self: RepairDiscordApi, method: str, path: str, payload: dict[str, str] | None = None) -> object:
        if method == "GET" and path == f"/channels/{CHANNEL_ID}":
            return {"id": CHANNEL_ID, "type": 11, "guild_id": GUILD_ID}
        if method == "GET" and path == f"/channels/{CHANNEL_ID}/messages/{MESSAGE_ID}":
            return {"id": MESSAGE_ID, "content": approval_request_content(pending)}
        if method == "GET" and "/reactions/" in path:
            return []
        if method == "POST":
            assert payload is not None
            posts.append((path, payload["content"]))
            return {"id": "1500000000000000004"}
        raise AssertionError(f"unexpected Discord request: {method} {path}")

    monkeypatch.setattr(RepairDiscordApi, "_api", fake_api)

    captured = []
    render = owner_message.render

    def capture(message, *, destination):
        captured.append(message)
        return render(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", capture)
    # When: the real repair watcher processes the pending approval.
    RepairApprovalWatcher(
        store, api, _Commands(), OWNER_ID, tmp_path / "audit.jsonl", lambda: NOW,
        ApprovalReminderConfig(initial_delay=timedelta(minutes=1), repeat_interval=timedelta(hours=1)),
    ).run_once()

    # The shared one-line reminder links the card even inside its own thread.
    assert len(posts) == 1
    assert posts[0][0] == f"/channels/{CHANNEL_ID}/messages"
    assert posts[0][1] == (
        f"⏰ 승인 대기 1시간 · https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{MESSAGE_ID}"
    )
    assert "@me" not in posts[0][1]
    assert captured == []

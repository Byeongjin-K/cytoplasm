"""Repair reminder wire coverage, separate from the oversized approval suites."""
from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Final, override
from urllib.request import Request

import pytest

from automation.interop import owner_message
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import (
    POLICY_VERSION, ApprovalBinding, ApprovalKind, ApprovalSurface, ChannelFacts, RequestThread,
)
from automation.repair import repair_command, repair_core, repair_ops_discord, repair_ops_reaction_watch
from automation.repair.repair_ops_approval import repair_action_hash
from automation.repair.repair_ops_pending import (
    PendingRepairApproval, PendingRepairApprovalStore, approval_request_content,
)
from automation.repair.repair_ops_reaction_watch import RepairApprovalWatcher

POSTED = datetime(2026, 9, 1, tzinfo=UTC)
PENDING = PendingRepairApproval(
    "repair-1", "patch.diff", repair_action_hash("repair-1", "patch.diff"),
    "nonce", "333", POSTED, kind=ApprovalKind.REPAIR,
    surface=ApprovalSurface.AGENT_CHAT_THREAD, channel_id="222",
    policy_version=POLICY_VERSION,
)
# Reminder wire bytes are shared minimum-information pointers, not approval cards.
LEGACY_FALLBACK: Final = "⏰ 승인 대기 3시간"
GUILD_FALLBACK: Final = (
    "⏰ 승인 대기 3시간 · https://discord.com/channels/111/222/333"
)


@dataclass(frozen=True, slots=True)
class Directory:
    def agent_chat(self) -> str:
        return "111"

    def owner_dm(self) -> str:
        pytest.fail("stored request must not resolve a new destination")

    def skill_approvals(self) -> str:
        pytest.fail("stored request must not resolve a new destination")

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        pytest.fail("stored request must not open a thread")

    def agent_chat_request_thread(self, kind: ApprovalKind, request: RequestThread) -> str:
        pytest.fail("stored request must not open a thread")

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == "222"
        return ChannelFacts(11, "repair", (), "111")


@dataclass(frozen=True, slots=True)
class Commands:
    def apply(self, pending: PendingRepairApproval) -> bool:
        pytest.fail("pending reminder must not apply")

    def discard(self, pending: PendingRepairApproval, reason: str) -> bool:
        pytest.fail("pending reminder must not discard")


@dataclass(frozen=True, slots=True)
class Response:
    body: bytes

    def read(self) -> bytes:
        return self.body


@dataclass(frozen=True, slots=True)
class Wire:
    """Record real transport requests; fake only Discord HTTP responses."""
    pending: PendingRepairApproval
    posts: list[tuple[str, str]] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)

    @contextmanager
    def open(self, request: Request) -> Iterator[Response]:
        path = request.full_url.removeprefix(repair_ops_discord.DISCORD_API)
        if request.method == "POST":
            assert isinstance(request.data, bytes)
            self.posts.append((path, json.loads(request.data)["content"]))
            yield Response(b'{"id":"444"}')
        else:
            assert request.method == "GET"
            self.reads.append(path)
            if path == "/channels/222":
                yield Response(b'{"id":"222","type":11}')
                return
            body = b"[]" if "/reactions/" in path else json.dumps(
                {"content": approval_request_content(self.pending)},
            ).encode()
            yield Response(body)


@pytest.fixture(params=[None, "111", ""])
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
            request: pytest.FixtureRequest) -> tuple[RepairApprovalWatcher, Wire]:
    store = PendingRepairApprovalStore(tmp_path / "pending")
    pending = replace(PENDING, approval_guild_id=request.param)
    store.save(pending)
    wire = Wire(pending)
    monkeypatch.setattr(repair_ops_discord, "_open_discord", wire.open)
    discord = repair_ops_discord.RepairDiscordApi(
        "credential", ApprovalBinding(ApprovalKind.REPAIR, ApprovalSurface.AGENT_CHAT_THREAD,
                                      "222", POLICY_VERSION), Directory(), "555",
    )
    watcher = RepairApprovalWatcher(
        store, discord, Commands(), "555", tmp_path / "audit.jsonl",
        lambda: POSTED + timedelta(hours=3), ApprovalReminderConfig(),
    )
    return watcher, wire


def test_reminder_preserves_legacy_bytes_when_envelope_import_is_unavailable(
    runtime: tuple[RepairApprovalWatcher, Wire], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an older release without the optional envelope module.
    watcher, wire = runtime
    expected = GUILD_FALLBACK if wire.pending.approval_guild_id else LEGACY_FALLBACK
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    # When: the real watcher ticks with a due, bound, unanswered request.
    watcher.run_once()
    # Then: the fallback preserves the independent golden wire bytes.
    assert wire.posts == [("/channels/222/messages", expected)]
    body = wire.posts[0][1]
    assert body.count("https://discord.com/channels/111/222/333") == int(bool(wire.pending.approval_guild_id))
    assert body.count("discord.com") == int(bool(wire.pending.approval_guild_id))
    assert "@me" not in body
    print(f"repair fallback guild={wire.pending.approval_guild_id!r}: {body}")


def test_reminder_does_not_send_when_disabled(runtime: tuple[RepairApprovalWatcher, Wire]) -> None:
    # Given: reminders explicitly disabled.
    watcher, wire = runtime
    disabled = replace(watcher, reminder_config=ApprovalReminderConfig(enabled=False))
    # When: the real watcher ticks.
    disabled.run_once()
    # Then: no notice is posted.
    assert wire.posts == []


@pytest.mark.parametrize("function, text, expected", [
    (repair_command.manual_location, "repair   fixture\nrequest",
     "gateway-command:6265a01165d3c0cf3c0889dd54cd78b7671d02faece11dcf5b324d6800013e6c"),
    (repair_core._error_class, "ValueError: fixture", "ValueError:"),
    (repair_core._error_class, "", "empty-error"),
])
def test_dedup_signature_bytes_stay_fixed_when_notice_rendering_changes(
    function: Callable[[str], str], text: str, expected: str,
) -> None:
    # Given: a fixed input to either protected dedup seam.
    # When: the signature component is produced.
    signature = function(text).encode()
    # Then: the machine-consumed bytes remain unchanged.
    assert signature == expected.encode()


@pytest.mark.parametrize("guild_id", [None, "666"])
def test_reminder_sends_pointer_without_rendering_an_approval_envelope(
    runtime: tuple[RepairApprovalWatcher, Wire], monkeypatch: pytest.MonkeyPatch,
    guild_id: str | None,
) -> None:
    # Given: a stored approval, including older records without guild metadata.
    watcher, _ = runtime
    pending = replace(PENDING, approval_guild_id=guild_id)
    watcher.store.save(pending)
    wire = Wire(pending)
    monkeypatch.setattr(repair_ops_discord, "_open_discord", wire.open)
    envelopes: list[tuple[owner_message.OwnerMessage, owner_message.Ref]] = []
    render = owner_message.render

    def capture(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        envelopes.append((message, destination))
        return render(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", capture)
    # When: the real watcher, scheduler, journal, binding and HTTP adapter run.
    watcher.run_once()
    # Then: reminders retain the card pointer, without approval-request reactions.
    assert envelopes == []
    assert len(wire.posts) == 1
    path, body = wire.posts[0]
    assert path == "/channels/222/messages"
    expected = "⏰ 승인 대기 3시간"
    if guild_id:
        expected += f" · https://discord.com/channels/{guild_id}/222/333"
    assert body == expected
    assert "@me" not in body
    assert len(body.splitlines()) == 1
    assert "✅" not in body and "⛔" not in body
    assert wire.reads.count("/channels/222") == int(guild_id is None)
    assert all(path == "/channels/222" or path.startswith("/channels/222/messages/333") for path in wire.reads)


def test_reminder_preserves_legacy_bytes_when_renderer_rejects_fields(
    runtime: tuple[RepairApprovalWatcher, Wire], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the optional renderer refuses a malformed envelope at its boundary.
    watcher, wire = runtime
    expected = GUILD_FALLBACK if wire.pending.approval_guild_id else LEGACY_FALLBACK

    def reject(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        raise owner_message.OwnerMessageError(detail="message.location")

    monkeypatch.setattr(owner_message, "render", reject)
    # When: a due reminder is sent.
    watcher.run_once()
    # Then: validation failure cannot black out today's notification.
    assert wire.posts == [("/channels/222/messages", expected)]


def test_reminder_refuses_incomplete_binding_when_thread_is_missing(
    runtime: tuple[RepairApprovalWatcher, Wire],
) -> None:
    # Given: a partial stored binding with no destination.
    watcher, wire = runtime
    pending = replace(PENDING, channel_id=None)
    # When / Then: the existing binding boundary refuses before any post.
    with pytest.raises(repair_ops_discord.RepairDiscordError):
        watcher._process(pending)
    assert wire.posts == []


@pytest.mark.parametrize("available", [True, False])
def test_entrypoint_posts_reminder_when_tick_is_due(
    runtime: tuple[RepairApprovalWatcher, Wire], monkeypatch: pytest.MonkeyPatch,
    available: bool,
) -> None:
    # Given: the real entry point with isolated state, a fixed clock and fake HTTP.
    watcher, wire = runtime
    expected = GUILD_FALLBACK if wire.pending.approval_guild_id else LEGACY_FALLBACK

    class Clock(datetime):
        @classmethod
        @override
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return (POSTED + timedelta(hours=3)).astimezone(tz)

    monkeypatch.setattr(repair_ops_reaction_watch, "datetime", Clock)
    monkeypatch.setattr(repair_ops_discord, "configured_discord", lambda: watcher.discord)
    monkeypatch.setattr(repair_ops_reaction_watch, "load_approval_reminder_config", ApprovalReminderConfig)
    monkeypatch.setenv("REPAIR_APPROVAL_PENDING_ROOT", str(watcher.store.root))
    monkeypatch.setenv("REPAIR_APPROVAL_LOG", str(watcher.approval_log))
    if not available:
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    # When: the systemd entry point runs one tick.
    status = repair_ops_reaction_watch.main()
    # Then: the real Discord adapter posts to the existing thread in either runtime.
    assert status == 0
    assert len(wire.posts) == 1
    path, body = wire.posts[0]
    assert path == "/channels/222/messages"
    assert body == expected


def test_reminder_delivers_without_link_when_injected_guild_is_non_string(
    runtime: tuple[RepairApprovalWatcher, Wire],
) -> None:
    # Given: malformed optional metadata injected after the strict store parser.
    watcher, wire = runtime
    pending = replace(wire.pending, approval_guild_id=111)
    # When: the real per-record watcher dispatches the reminder.
    watcher._process(pending)
    # Then: the renderer fallback still delivers without a guessed link.
    [(_, body)] = wire.posts
    assert "@me" not in body and "discord.com" not in body


def test_explicit_dm_binding_retains_legacy_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pending = replace(PENDING, surface=ApprovalSurface.OWNER_DM, policy_version=5)
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(pending)
    wire = Wire(pending)
    monkeypatch.setattr(repair_ops_discord, "_open_discord", wire.open)
    monkeypatch.setattr(Directory, "describe", lambda self, channel: ChannelFacts(1, "", ("555",)))
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    discord = repair_ops_discord.RepairDiscordApi(
        "credential", ApprovalBinding(ApprovalKind.REPAIR, ApprovalSurface.OWNER_DM, "222", 5),
        Directory(), "555",
    )
    RepairApprovalWatcher(store, discord, Commands(), "555", tmp_path / "audit.jsonl",
                          lambda: POSTED + timedelta(hours=3), ApprovalReminderConfig()).run_once()
    [(path, body)] = wire.posts
    assert path == "/channels/222/messages"
    assert body.count("https://discord.com/channels/@me/222/333") == 1

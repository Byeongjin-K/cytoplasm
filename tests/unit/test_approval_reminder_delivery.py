"""Direct-link and minimum-information approval reminder boundary."""
from __future__ import annotations

from datetime import timedelta

import pytest

from automation.interop.approval_reminder import (
    ApprovalReminder,
    DeliveryRoute,
    DeliveryScope,
    DeliveryTarget,
    DiscordSource,
    LinkStatus,
    ReminderBoundaryError,
    SourceLifecycle,
    SourceReference,
    authorize_delivery,
    compose_reminder,
    discord_message_link,
    resolve_source_link,
)
from automation.interop.approval_surface import ApprovalKind


def test_discord_guild_channel_link() -> None:
    assert discord_message_link(
        DiscordSource(channel_id="222", message_id="333", guild_id="111")
    ).url == "https://discord.com/channels/111/222/333"


def test_discord_thread_uses_same_canonical_link_shape() -> None:
    assert discord_message_link(
        DiscordSource(channel_id="444", message_id="555", guild_id="111")
    ).url == "https://discord.com/channels/111/444/555"


def test_discord_dm_link_uses_me_route() -> None:
    result = discord_message_link(DiscordSource(channel_id="222", message_id="333", space="dm"))
    assert result.url == "https://discord.com/channels/@me/222/333"
    assert result.status is LinkStatus.AVAILABLE


@pytest.mark.parametrize("channel_id,message_id,guild_id", [("", "2", None), ("x", "2", None), ("1", "x", None), ("1", "2", "x")])
def test_discord_link_rejects_malformed_identifiers(
    channel_id: str, message_id: str, guild_id: str | None
) -> None:
    result = discord_message_link(DiscordSource(channel_id, message_id, guild_id, space="dm"))
    assert result.status is LinkStatus.INVALID
    assert result.url is None
    assert "x" not in (result.detail or "")


def test_existing_non_discord_permalink_is_preserved() -> None:
    result = resolve_source_link(
        SourceReference(platform="slack", permalink="https://workspace.example/archives/C1/p2")
    )
    assert result.status is LinkStatus.AVAILABLE
    assert result.url == "https://workspace.example/archives/C1/p2"


def test_non_discord_source_without_permalink_is_explicitly_unavailable() -> None:
    result = resolve_source_link(SourceReference(platform="mailon"))
    assert result.status is LinkStatus.UNAVAILABLE
    assert result.url is None
    assert result.detail == "this approval surface does not provide a direct link"


def test_deleted_source_is_reported_without_constructing_link() -> None:
    result = resolve_source_link(
        SourceReference(
            platform="discord",
            discord=DiscordSource("222", "333", "111"),
            lifecycle=SourceLifecycle.DELETED,
        )
    )
    assert result.status is LinkStatus.DELETED
    assert result.url is None


def test_reminder_contains_only_type_elapsed_and_link() -> None:
    reminder = ApprovalReminder(
        request_type=ApprovalKind.MAIL_COMPOSE,
        elapsed=timedelta(hours=4, minutes=7),
        source_url="https://discord.com/channels/@me/222/333",
    )
    body = compose_reminder(reminder)
    assert body == (
        "⏰ 승인 대기 4시간 · [승인 카드](<https://discord.com/channels/@me/222/333>)"
    )
    for sensitive in ("recipient@example.com", "/tmp/attachment.pdf", "--execute", "제목:"):
        assert sensitive not in body


@pytest.mark.parametrize("bad_type", ["", "line\nbreak", "recipient@example.com"])
def test_request_type_is_bounded_single_line(bad_type: str) -> None:
    with pytest.raises(ReminderBoundaryError):
        compose_reminder(ApprovalReminder(bad_type, timedelta(hours=3), "https://example.test/a"))  # type: ignore[arg-type]


def test_negative_elapsed_and_non_https_link_are_rejected() -> None:
    with pytest.raises(ReminderBoundaryError):
        compose_reminder(ApprovalReminder(ApprovalKind.CALENDAR, timedelta(seconds=-1), "https://example.test/a"))
    with pytest.raises(ReminderBoundaryError):
        compose_reminder(ApprovalReminder(ApprovalKind.CALENDAR, timedelta(hours=3), "file:///secret"))


def test_delivery_is_confined_to_owner_dm_or_original_channel() -> None:
    scope = DeliveryScope(
        source_channel_id="222",
        owner_dm_channel_id="999",
    )
    assert authorize_delivery(scope, DeliveryTarget("999", DeliveryRoute.OWNER))
    assert authorize_delivery(scope, DeliveryTarget("222", DeliveryRoute.ORIGINAL))


@pytest.mark.parametrize(
    "target",
    [
        DeliveryTarget("333", DeliveryRoute.ORIGINAL),
        DeliveryTarget("222", DeliveryRoute.OWNER),
        DeliveryTarget("999", DeliveryRoute.ORIGINAL),
    ],
)
def test_delivery_rejects_scope_expansion_or_surface_spoof(target: DeliveryTarget) -> None:
    scope = DeliveryScope("222", "999")
    assert not authorize_delivery(scope, target)

"""Owner-notice envelope compatibility; separate from FS3-pinned sender inventory."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from inspect import Parameter, signature
from typing import Final, Protocol
from unittest.mock import patch

import pytest

from automation import owner_notice
from automation.interop import owner_message
from automation.interop.owner_message import Action, OwnerMessage, Periodic, Ref

_UNCONFIGURED: Final = (
    "[owner-notice] NOTIFY-UNCONFIGURED: owner credential missing, notice not sent\n"
)
_IMPORT = __import__


def _without_envelope(name, *args, **kwargs):
    # Simulate the missing optional renderer, not missing stdlib modules such as
    # ntpath (Path construction imports it when the sender checks its override).
    if name == "automation.interop.owner_message":
        raise ImportError("unavailable")
    return _IMPORT(name, *args, **kwargs)


class NoticeFacade(Protocol):
    def __call__(
        self, content: str | None = None, *, notice: str | None = None,
        message: OwnerMessage | None = None,
    ) -> bool: ...


@pytest.fixture(params=[owner_notice.notify_owner, owner_notice.notify_owner_dm])
def facade(request: pytest.FixtureRequest) -> NoticeFacade:
    return request.param


@pytest.fixture(autouse=True)
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    attempts: list[tuple[str, str]] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "444")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "555")
    monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda token, owner: "666")
    monkeypatch.setattr(
        owner_notice, "send_notice",
        lambda token, channel, body: attempts.append((channel, body)),
    )
    return attempts


def test_legacy_bytes_when_content_is_positional(
    facade: NoticeFacade, sent: list[tuple[str, str]],
) -> None:
    # Given: whitespace and Unicode must survive unchanged.
    content = "  통지\t본문\r\nsecond line\n"
    # When
    delivered = facade(content)
    # Then: payload preservation, not a prose assertion.
    assert delivered is True
    assert [body.encode() for _, body in sent] == [content.encode()]


def test_unconfigured_marker_when_credentials_are_absent(
    facade: NoticeFacade, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: no local config can fill the missing credential.
    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    monkeypatch.delenv("AUTOPHAGY_OWNER_ID")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "")
    monkeypatch.setattr(owner_notice, "_config_owner_id", lambda: "")
    # When
    with patch.object(owner_notice, "send_notice") as sender:
        delivered = facade("legacy")
    # Then: the full existing journal marker is a compatibility contract.
    assert delivered is False
    assert capsys.readouterr().err == _UNCONFIGURED
    sender.assert_not_called()


def test_failed_marker_when_transport_raises(
    facade: NoticeFacade, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    with patch.object(owner_notice, "send_notice", side_effect=RuntimeError("offline")):
        # When
        delivered = facade("legacy")
    # Then
    assert delivered is False
    assert capsys.readouterr().err == "[owner-notice] NOTIFY-FAILED: RuntimeError\n"


def test_failed_marker_when_dm_open_raises(
    facade: NoticeFacade, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: normal notices also use DM when the channel is absent.
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "")
    with patch.object(owner_notice, "owner_dm_channel", side_effect=OSError("offline")):
        # When
        delivered = facade("legacy")
    # Then
    assert delivered is False
    assert capsys.readouterr().err == "[owner-notice] NOTIFY-FAILED: OSError\n"


@pytest.fixture
def message() -> OwnerMessage:
    return OwnerMessage(
        subject_key="report-1", subject="보고", fact="관측 완료",
        location=Ref(scope="message", space="guild", guild_id="111",
                     channel_id="222", message_id="333"),
        owner=Action("none"), agent_next=None, recovery="not_applicable",
        detail=Periodic(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)),
    )


def test_link_when_capability_aware_caller_supplies_envelope(
    facade: NoticeFacade, message: OwnerMessage, sent: list[tuple[str, str]],
) -> None:
    # Given: migrated callers probe this flag on mixed-version runtimes.
    accepts = getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False)
    # When
    delivered = facade("legacy", message=message) if accepts else facade("legacy")
    # Then: a notice is outside the referenced source thread.
    assert delivered is True
    assert len(sent) == 1
    assert sent[0][1].count("https://discord.com/channels/111/222/333") == 1
    assert sent[0][0] == ("555" if facade is owner_notice.notify_owner else "666")


def test_false_when_capability_aware_caller_supplies_unsupported_version(
    facade: NoticeFacade, message: OwnerMessage, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the producer runs a newer renderer contract than the facade.
    broken = replace(message, render_version="owner-ko-v999")
    accepts = getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False)
    # When
    delivered = facade("legacy", message=broken) if accepts else facade("legacy")
    # Then: the caller can queue and retry; no exception escapes.
    assert delivered is False
    assert capsys.readouterr().err == "[owner-notice] NOTIFY-FAILED: OwnerMessageError\n"


def test_keyword_envelope_when_facade_advertises_capability(facade: NoticeFacade) -> None:
    # Given
    parameters = signature(facade).parameters
    # When
    accepts = getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False)
    # Then: either body keyword binds; the envelope and legacy alias are keyword-only.
    assert accepts is True
    assert parameters["content"].default is None
    assert parameters["notice"].kind is Parameter.KEYWORD_ONLY
    assert parameters["notice"].default is None
    assert parameters["message"].kind is Parameter.KEYWORD_ONLY
    assert parameters["message"].default is None


@pytest.mark.parametrize("keyword", ["notice", "content"])
def test_body_keywords_preserve_bytes(
    facade: NoticeFacade, keyword: str, sent: list[tuple[str, str]],
) -> None:
    content = "  body\t\r\n"
    delivered = facade(notice=content) if keyword == "notice" else facade(content=content)
    assert delivered is True
    assert [body for _, body in sent] == [content]


def test_legacy_keyword_accepts_envelope(
    facade: NoticeFacade, message: OwnerMessage, sent: list[tuple[str, str]],
) -> None:
    assert facade(notice="legacy", message=message) is True
    assert len(sent) == 1
    assert "https://discord.com/channels/111/222/333" in sent[0][1]


@pytest.mark.parametrize("conflicting", [False, True])
def test_missing_or_conflicting_bodies_return_false(
    facade: NoticeFacade, conflicting: bool, sent: list[tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    delivered = facade(content="first", notice="second") if conflicting else facade()
    assert delivered is False
    assert sent == []
    assert capsys.readouterr().err == "[owner-notice] NOTIFY-FAILED: TypeError\n"


def test_identical_body_aliases_deliver_once(
    facade: NoticeFacade, sent: list[tuple[str, str]],
) -> None:
    assert facade(content="body", notice="body") is True
    assert [body for _, body in sent] == ["body"]


def test_legacy_bytes_when_message_is_none(
    facade: NoticeFacade, sent: list[tuple[str, str]],
) -> None:
    # Given
    content = "  legacy\t\r\n"
    # When
    delivered = facade(content, message=None)
    # Then
    assert delivered is True
    assert [body.encode() for _, body in sent] == [content.encode()]


def test_destination_when_rendering_envelope(facade: NoticeFacade, message: OwnerMessage) -> None:
    # Given: the destination, not the source thread, drives renderer locality.
    destination = (Ref(scope="channel", space="unknown", channel_id="555")
                   if facade is owner_notice.notify_owner
                   else Ref(scope="channel", space="dm", channel_id="666"))
    with patch.object(owner_message, "render", wraps=owner_message.render) as renderer:
        # When
        delivered = facade("legacy", message=message)
    # Then: the real renderer runs, and it receives the resolved transport target.
    assert delivered is True
    renderer.assert_called_once_with(message, destination=destination)


def test_dm_link_when_notice_channel_is_absent(
    message: OwnerMessage, monkeypatch: pytest.MonkeyPatch, sent: list[tuple[str, str]],
) -> None:
    # Given
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "")
    # When
    delivered = owner_notice.notify_owner("legacy", message=message)
    # Then
    assert delivered is True
    assert sent[0][0] == "666"
    assert sent[0][1].count("https://discord.com/channels/111/222/333") == 1


@pytest.mark.parametrize("field", ["subject", "fact", "location", "owner", "detail"])
def test_false_when_required_field_is_none(
    facade: NoticeFacade, message: OwnerMessage, field: str,
) -> None:
    # Given: deliberately malformed runtime input at the renderer boundary.
    broken = replace(message, **{field: None})
    with patch.object(owner_notice, "send_notice") as sender:
        # When
        delivered = facade("legacy", message=broken)
    # Then: never send the legacy fallback after a rendering failure.
    assert delivered is False
    sender.assert_not_called()


def test_false_when_renderer_raises_unexpected_error(
    facade: NoticeFacade, message: OwnerMessage, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    with patch.object(owner_message, "render", side_effect=RuntimeError("broken")):
        # When
        delivered = facade("legacy", message=message)
    # Then
    assert delivered is False
    assert capsys.readouterr().err == "[owner-notice] NOTIFY-FAILED: RuntimeError\n"


def test_false_when_envelope_import_is_unavailable(
    facade: NoticeFacade, message: OwnerMessage, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an old runtime cannot load the envelope module.
    with patch("builtins.__import__", side_effect=_without_envelope):
        # When
        delivered = facade("legacy", message=message)
    # Then
    assert delivered is False
    assert capsys.readouterr().err == "[owner-notice] NOTIFY-FAILED: ImportError\n"


def test_legacy_delivery_when_envelope_import_is_unavailable(facade: NoticeFacade) -> None:
    # Given
    with patch("builtins.__import__", side_effect=_without_envelope):
        # When
        delivered = facade("legacy")
    # Then: the string-only route does not load the new dependency.
    assert delivered is True


def test_unconfigured_marker_when_broken_envelope_has_no_credentials(
    facade: NoticeFacade, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: credentials gate precedes even a renderer that would fail.
    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    with patch.object(owner_message, "render", side_effect=RuntimeError("broken")) as renderer:
        # When
        delivered = facade("legacy", message=OwnerMessage.__new__(OwnerMessage))
    # Then
    assert delivered is False
    assert capsys.readouterr().err == _UNCONFIGURED
    renderer.assert_not_called()

"""Discord labels must not depend on the reader's cached thread names."""
from __future__ import annotations

from automation.interop import owner_message
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result, render_report


def test_masked_link_keeps_coordinates_and_removes_label_brackets() -> None:
    url = owner_message.discord_link(
        space="guild", guild_id="111", channel_id="222", message_id="333",
    ).url
    assert url is not None
    assert owner_message.masked_discord_link(url, "[승인] 카드") == (
        "[승인 카드](<https://discord.com/channels/111/222/333>)"
    )


def test_masked_link_keeps_a_nonempty_single_line_label() -> None:
    assert owner_message.masked_discord_link(
        "https://discord.com/channels/111/222", " []\n ",
    ) == "[스레드](<https://discord.com/channels/111/222>)"


def test_pair_report_masks_its_original_message_reference() -> None:
    message = OwnerMessage(
        subject_key="sample", subject="점검", fact="점검을 마쳤습니다",
        location=Ref(scope="message", space="guild", guild_id="111",
                     channel_id="222", message_id="333"),
        owner=Action("none"), agent_next=None, recovery="not_applicable",
        detail=Result("executed"),
    )
    report = render_report(message, destination=Ref(scope="channel", channel_id="444"))
    assert "[원글](<https://discord.com/channels/111/222/333>)" in report
